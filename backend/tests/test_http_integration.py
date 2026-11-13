"""Real HTTP + PostgreSQL checks in a disposable, uniquely named schema.

Set PULT_TEST_DATABASE_URL explicitly. Never uses production tables.
MinIO round-trip also runs when PULT_MINIO_ENDPOINT is configured.
"""
import io
import json
import os
import socket
import threading
import time
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4


class Client:
    def __init__(self, base):
        self.base = base
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.access = None

    def call(self, path, method="GET", body=None, *, browser_header=True, raw=None):
        headers = {"X-Pult-Auth": "1"} if browser_header else {}
        if self.access:
            headers["Authorization"] = f"Bearer {self.access}"
        if body is not None:
            headers["Content-Type"] = "application/json"
            raw = json.dumps(body).encode()
        request = Request(self.base + path, data=raw, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            result = json.loads(content) if content and "application/json" in response.headers.get("Content-Type", "") else content
            if isinstance(result, dict) and "access_token" in result:
                self.access = result["access_token"]
            return response.status, result, response.headers


@unittest.skipUnless(os.getenv("PULT_TEST_DATABASE_URL"), "set PULT_TEST_DATABASE_URL for isolated PostgreSQL integration")
class HttpIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        import uvicorn
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from backend.main import create_app
        from backend.pipeline.results import PostgresResults
        from backend.services.auth import AuthStore

        cls.schema = "pult_test_" + uuid4().hex
        cls.base_connection = os.environ["PULT_TEST_DATABASE_URL"]
        with psycopg.connect(cls.base_connection, autocommit=True) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
        cls.addClassCleanup(cls.drop_schema)
        connection = make_conninfo(cls.base_connection, options=f"-c search_path={cls.schema}")
        cls.database = PostgresResults(connection)
        cls.database.initialize()
        cls.auth = AuthStore(cls.database)
        cls.auth.initialize()
        cls.environment = patch.dict(os.environ, {"PULT_DATABASE_URL": connection, "PULT_AUTH_SECURE_COOKIE": "0"})
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        cls.server = uvicorn.Server(uvicorn.Config(create_app(Path(cls.temporary.name)), log_level="error"))
        cls.thread = threading.Thread(target=cls.server.run, kwargs={"sockets": [sock]}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.stop_server)
        deadline = time.monotonic() + 10
        while not cls.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not cls.server.started:
            raise RuntimeError("Test HTTP server did not start")

    @classmethod
    def stop_server(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)

    @classmethod
    def drop_schema(cls):
        import psycopg
        from psycopg import sql
        assert cls.schema.startswith("pult_test_") and len(cls.schema) == 42
        with psycopg.connect(cls.base_connection, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)))

    def setUp(self):
        with self.database.connection() as db:
            db.execute("DELETE FROM auth_users")
            db.execute("DELETE FROM auth_attempts")
            db.execute("DELETE FROM monitoring_decisions")
            db.execute("DELETE FROM forecast_observations")
            db.execute("DELETE FROM drift_reports")
            db.execute("DELETE FROM drift_references")
            db.execute("DELETE FROM active_forecast_model")
            db.execute("DELETE FROM model_activations")
            db.execute("DELETE FROM model_candidates")
            db.execute("DELETE FROM experiments")
            db.execute("DELETE FROM model_versions")

    def activation(self, version_id, previous=None, action="initialize", hour=0):
        return {"activation_id": f"20261113T{hour:02d}0000000000Z-00000000", "action": action,
                "version_id": version_id, "previous_version_id": previous, "candidate_id": None, "actor": "test",
                "reason": "", "at_utc": f"2026-11-13T{hour:02d}:00:00+00:00", "context": {"reference": {}}}

    def test_registry_reads_metadata_and_failed_publication_rolls_back_model(self):
        from datetime import date

        from backend.pipeline.results import EVALUATIONS, ModelVersion, StorageUnavailable

        target = date(2026, 11, 13)
        first_id = "20261113T120000000000Z-000000000001"
        second_id = "20261113T120000000000Z-000000000002"
        stored = ModelVersion({"version_id": first_id, "status": "registered"}, b"test artifact")
        failed = ModelVersion({"version_id": second_id, "status": "published"}, b"test artifact 2")
        self.database.save_model(stored)
        run = {"run_id": "test-registry-transaction"}
        self.database.save_run(run)
        try:
            with self.assertRaises(StorageUnavailable):
                self.database.publish(EVALUATIONS, target, {"model_version_id": second_id}, run, model=failed)
            self.assertFalse(self.database.has(EVALUATIONS, target))
            with self.assertRaises(FileNotFoundError):
                self.database.model_version(second_id)
            self.assertEqual(self.database.model_version(first_id), stored)

            client = Client(self.base)
            status, body, _ = client.call("/api/models?limit=1")
            self.assertEqual(status, 200)
            self.assertEqual({key: body[key] for key in ("versions", "next_before", "active_version")},
                             {"versions": [stored.metadata], "next_before": None, "active_version": None})
            self.assertEqual((body["lifecycle"]["candidates"], body["lifecycle"]["activations"]), ([], []))
            self.assertEqual(client.call(f"/api/models?before={first_id}")[1]["versions"], [])
            self.assertEqual(client.call("/api/models?before=bad")[0], 422)

            report = {"target_date": target.isoformat(), "model_version_id": second_id,
                      "selected": {"model": "ridge", "uses_weather": False},
                      "forecast_cutoff": "2026-11-12T20:00:00+03:00", "orders_source": "synthetic",
                      "weather": {}, "forecasts": [], "validation": [], "holdout": {}}
            self.database.publish(EVALUATIONS, target, report, model=failed)
            self.assertTrue(self.database.activate_model(self.activation(first_id), expected=None))
            self.assertFalse(self.database.activate_model(self.activation(second_id, hour=1), expected=None))
            self.assertFalse(self.database.activate_model(self.activation(second_id, first_id, "activate", 1),
                                                          expected=second_id), "stale expectation")
            status, dashboard, _ = client.call(f"/api/dashboard?date={target}")
            self.assertEqual(status, 200)
            self.assertEqual(dashboard["forecast"]["model_version_id"], second_id)
            self.assertEqual(dashboard["forecast_model"], failed.metadata)
            status, page, _ = client.call("/api/models?limit=1")
            self.assertEqual((status, page["next_before"]), (200, second_id))
            self.assertEqual(page["active_version"], stored.metadata)
            self.assertEqual(client.call(f"/api/models?before={second_id}")[1]["versions"], [stored.metadata])
            self.assertNotIn("lifecycle", client.call(f"/api/models?before={second_id}")[1])
        finally:
            with self.database.connection() as connection:
                connection.execute("DELETE FROM pipeline_runs WHERE run_id = %s", (run["run_id"],))
                connection.execute("DELETE FROM forecast_reports WHERE kind = %s AND target_date = %s", (EVALUATIONS, target))

    def test_activation_stores_a_new_version_atomically(self):
        from backend.pipeline.results import ModelVersion

        first = ModelVersion({"version_id": "20261101T120000000000Z-000000000001"}, b"first")
        second = ModelVersion({"version_id": "20261108T120000000000Z-000000000002"}, b"second")
        record = self.activation(first.metadata["version_id"])
        self.assertTrue(self.database.activate_model(record, expected=None, model=first), "clean stand: version and pointer together")
        self.assertEqual(self.database.model_version(first.metadata["version_id"]), first)
        stale = self.activation(second.metadata["version_id"], first.metadata["version_id"], "activate", 1)
        self.assertFalse(self.database.activate_model(stale, expected=second.metadata["version_id"], model=second))
        with self.assertRaises(FileNotFoundError):
            self.database.model_version(second.metadata["version_id"])
        self.assertEqual([item["action"] for item in self.database.activations()], ["initialize"])

        job = {"candidate_id": "20261108T000000000000Z-00000001", "trigger_key": "monitoring:2026-11-07", "status": "queued",
               "trigger": {"kind": "monitoring", "end_date": "2026-11-07"}, "active_version_id": first.metadata["version_id"]}
        self.assertEqual(self.database.queue_candidate(job), (job, True))
        other = {**job, "candidate_id": "20261108T000000000000Z-00000002", "trigger_key": "manual:2026-11-07"}
        self.assertEqual(self.database.queue_candidate(other), (job, False), "single open job across processes")
        accepted = {**job, "status": "activated", "version_id": second.metadata["version_id"]}
        self.assertTrue(self.database.activate_model(stale, expected=first.metadata["version_id"], model=second, candidate=accepted))
        self.assertEqual(self.database.active_model()["version_id"], second.metadata["version_id"])
        self.assertEqual(self.database.candidates(), [accepted])
        self.assertIsNone(self.database.open_candidate())
        self.assertEqual(self.database.queue_candidate(other)[1], True, "a new job once the previous one is closed")

    def test_model_actions_require_admin_and_never_duplicate_or_lose_versions(self):
        from datetime import datetime, timedelta

        from backend.pipeline.orders import MOSCOW
        from backend.pipeline.results import ModelVersion

        first_id = "20261101T120000000000Z-000000000001"
        second_id = "20261108T120000000000Z-000000000002"
        yesterday = datetime.now(MOSCOW).date() - timedelta(days=1)
        from backend.pipeline.orders import LEGACY_SOURCE, SOURCE

        first = ModelVersion({"version_id": first_id, "training": {"end": "2026-01-01"}, "orders_source": SOURCE}, b"first")
        second = ModelVersion({"version_id": second_id, "training": {"end": yesterday.isoformat()}, "orders_source": SOURCE},
                              b"second")
        for model in (first, second):
            self.database.save_model(model)
        self.assertTrue(self.database.activate_model(self.activation(first_id), expected=None))
        self.assertTrue(self.database.activate_model(self.activation(second_id, first_id, "activate", 1), expected=first_id))

        user, _ = self.register("qa_user")
        admin, _ = self.register("qa_admin")
        self.auth.promote("qa_admin")
        anonymous = Client(self.base)
        for path, body in (("/api/admin/models/candidates", None),
                           ("/api/admin/models/rollback", {"expected_version_id": second_id})):
            self.assertEqual(anonymous.call(path, "POST", body)[0], 401)
            self.assertEqual(user.call(path, "POST", body)[0], 403)
            self.assertEqual(admin.call(path, "POST", body, browser_header=False)[0], 403)

        status, body, _ = admin.call("/api/admin/models/candidates", "POST")
        self.assertEqual(status, 409)
        self.assertIn("Честная контрольная неделя", body["detail"])
        self.assertIn("Честная", anonymous.call("/api/models")[1]["lifecycle"]["manual_check"]["issue"])

        with self.database.connection() as db:
            db.execute("UPDATE model_versions SET metadata = jsonb_set(metadata, '{training,end}', to_jsonb(%s::text))",
                       ("2026-01-01",))
        status, body, _ = admin.call("/api/admin/models/candidates", "POST")
        self.assertEqual((status, body["created"], body["candidate"]["status"]), (202, True, "queued"))
        self.assertEqual(body["candidate"]["trigger"]["requested_by"], "admin:qa_admin")
        repeat = admin.call("/api/admin/models/candidates", "POST")
        self.assertEqual((repeat[0], repeat[1]["created"], repeat[1]["candidate"]["candidate_id"]),
                         (200, False, body["candidate"]["candidate_id"]))
        self.assertEqual(len(anonymous.call("/api/models")[1]["lifecycle"]["candidates"]), 1)

        self.assertEqual(admin.call("/api/admin/models/rollback", "POST", {"expected_version_id": first_id})[0], 409)
        self.assertEqual(admin.call("/api/admin/models/rollback", "POST", {"expected_version_id": "bad"})[0], 422)
        status, body, _ = admin.call("/api/admin/models/rollback", "POST", {"expected_version_id": second_id})
        self.assertEqual(status, 200, body)
        self.assertEqual((body["activation"]["action"], body["activation"]["version_id"]), ("rollback", first_id))
        page = anonymous.call("/api/models")[1]
        self.assertEqual(page["active_version"]["version_id"], first_id)
        self.assertEqual(page["lifecycle"]["rollback_to"]["version_id"], second_id)
        self.assertEqual([item["action"] for item in page["lifecycle"]["activations"]], ["rollback", "activate", "initialize"])
        self.assertNotIn("context", page["lifecycle"]["activations"][0])
        self.assertEqual(self.database.active_model()["reference"], {})

        # A version trained on an older data version is never offered or restored.
        with self.database.connection() as db:
            db.execute("UPDATE model_versions SET metadata = jsonb_set(metadata, '{orders_source}', to_jsonb(%s::text)) "
                       "WHERE version_id = %s", (LEGACY_SOURCE, second_id))
        self.assertIsNone(anonymous.call("/api/models")[1]["lifecycle"]["rollback_to"])
        status, body, _ = admin.call("/api/admin/models/rollback", "POST", {"expected_version_id": first_id})
        self.assertEqual(status, 409)
        self.assertIn("другой версии данных", body["detail"])

    def test_dashboard_reads_saved_drift_and_repeat_preserves_the_report(self):
        from backend.pipeline.drift import RULE_VERSION, persist_input_drift
        from backend.tests.test_drift import NOW, sample_days

        data = Path(self.temporary.name)
        with patch("backend.pipeline.drift.load_days", side_effect=sample_days):
            report = persist_input_drift(self.database, data, NOW)
            self.assertIsNone(persist_input_drift(self.database, data, NOW))
        client = Client(self.base)
        status, dashboard, _ = client.call("/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(dashboard["input_drift"], report)
        self.assertEqual(self.database.drift_reference(RULE_VERSION)["window"], report["reference"])
        self.assertEqual(self.database.latest_drift(RULE_VERSION), report)

    def test_monitoring_decisions_survive_connections_and_api_reads_do_not_write(self):
        from backend.pipeline.monitoring import RULE_VERSION, decision_records
        from backend.pipeline.results import PostgresResults
        from backend.tests.test_monitoring import NOW, observations

        records = list(decision_records(observations([20] * 14), NOW))
        self.assertEqual(self.database.save_decisions(records), 14)
        self.assertEqual(PostgresResults(self.database.connection_info).save_decisions(records), 0)
        client = Client(self.base)
        status, first, _ = client.call("/api/monitoring/decisions?zone=east&limit=5")
        self.assertEqual(status, 200)
        self.assertEqual(first["decisions"][0]["status"], "alert")
        self.assertEqual(first["next_before"], "2026-10-10")
        other = Client(self.base)
        self.assertEqual(other.call("/api/monitoring/decisions?zone=east&limit=5")[1], first)
        second = other.call("/api/monitoring/decisions?zone=east&limit=5&before=2026-10-10")[1]
        self.assertEqual(second["decisions"][0]["end_date"], "2026-10-09")
        self.assertEqual(client.call("/api/monitoring/decisions?zone=north")[1]["decisions"], [])
        for query in ("zone=unknown", "zone=../east", "zone=east&limit=31", "zone=east&before=bad"):
            self.assertEqual(client.call("/api/monitoring/decisions?" + query)[0], 422)
        current = client.call("/api/dashboard")[1]["monitoring_decisions"]
        self.assertEqual(current, [records[-1]])
        self.assertEqual(len(self.database.decisions(RULE_VERSION, "east")), 14)
        # An attempted repeat must not alter a historical conclusion or its timestamp.
        self.assertEqual(self.database.save_decisions([{**records[-1], "status": "normal"}]), 0)
        self.assertEqual(self.database.decisions(RULE_VERSION, "east", 1), [records[-1]])

    def register(self, login="qa_user"):
        client = Client(self.base)
        status, body, headers = client.call("/api/auth/register", "POST", {"login": login, "password": "test-password-only-42"})
        self.assertEqual(status, 201, body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=strict", headers["Set-Cookie"])
        return client, body["user"]

    def test_refresh_after_reload_expiry_and_replay_rejection(self):
        client, user = self.register()
        original_cookie = next(iter(client.cookies)).value
        for _ in range(3):
            client.access = None  # A reload loses the in-memory access token.
            status, body, _ = client.call("/api/auth/refresh", "POST")
            self.assertEqual(status, 200)
            self.assertEqual(body["user"]["id"], user["id"])
        with self.database.connection() as db:
            db.execute("UPDATE auth_access_tokens SET expires_at = now() - interval '1 second'")
        self.assertEqual(client.call("/api/auth/me")[0], 401)
        self.assertEqual(client.call("/api/auth/refresh", "POST")[0], 200)
        self.assertEqual(client.call("/api/auth/me")[0], 200)
        # Guessing a secret for a known session cannot log its owner out.
        valid_cookie = next(iter(client.cookies)).value
        next(iter(client.cookies)).value = valid_cookie.split(".")[0] + "." + "A" * 43
        self.assertEqual(client.call("/api/auth/refresh", "POST")[0], 401)
        next(iter(client.cookies)).value = valid_cookie
        self.assertEqual(client.call("/api/auth/refresh", "POST")[0], 200)
        next(iter(client.cookies)).value = original_cookie
        self.assertEqual(client.call("/api/auth/refresh", "POST")[0], 401)
        self.assertEqual(client.call("/api/auth/me")[0], 401)

    def test_profile_shared_between_sessions_and_validation(self):
        first, _ = self.register()
        second = Client(self.base)
        self.assertEqual(second.call("/api/auth/login", "POST", {"login": "QA_USER", "password": "test-password-only-42"})[0], 200)
        profile = {"display_name": "Диспетчер", "bio": "Проверка общего профиля", "birthday": "2000-01-02", "preferred_zone": "north"}
        self.assertEqual(first.call("/api/auth/profile", "PUT", profile)[0], 200)
        status, saved, _ = second.call("/api/auth/profile")
        self.assertEqual(status, 200)
        self.assertEqual(saved["bio"], profile["bio"])
        self.assertEqual(saved["account"]["preferred_zone"], "north")
        self.assertEqual(saved["active_sessions"], 2)
        self.assertEqual(second.call("/api/auth/profile", "PUT", {**profile, "birthday": "2999-01-01"})[0], 422)
        self.assertEqual(second.call("/api/auth/profile", "PUT", {**profile, "preferred_zone": "unknown"})[0], 422)
        self.assertEqual(first.call("/api/auth/logout", "POST")[0], 204)
        self.assertEqual(first.call("/api/auth/me")[0], 401)
        self.assertEqual(second.call("/api/auth/me")[0], 200)

    def test_password_change_revokes_every_session(self):
        first, _ = self.register()
        second = Client(self.base)
        second.call("/api/auth/login", "POST", {"login": "qa_user", "password": "test-password-only-42"})
        body = {"current_password": "incorrect", "new_password": "new-test-password-only-43"}
        self.assertEqual(first.call("/api/auth/change-password", "POST", body)[0], 400)
        self.assertEqual(second.call("/api/auth/me")[0], 200)
        body["current_password"] = "test-password-only-42"
        self.assertEqual(first.call("/api/auth/change-password", "POST", body)[0], 204)
        for client in (first, second):
            self.assertEqual(client.call("/api/auth/me")[0], 401)
            self.assertEqual(client.call("/api/auth/refresh", "POST")[0], 401)
        self.assertEqual(first.call("/api/auth/login", "POST", {"login": "qa_user", "password": "test-password-only-42"})[0], 401)
        self.assertEqual(first.call("/api/auth/login", "POST", {"login": "qa_user", "password": body["new_password"]})[0], 200)

    def test_admin_block_and_unblock_do_not_restore_old_sessions(self):
        user, account = self.register()
        admin, admin_account = self.register("qa_admin")
        self.auth.promote("qa_admin")
        self.assertEqual(user.call("/api/admin/users")[0], 403)
        self.assertEqual(Client(self.base).call("/api/admin/users")[0], 401)
        status, page, _ = admin.call("/api/admin/users?q=qa_user")
        self.assertEqual(status, 200)
        self.assertEqual([entry["id"] for entry in page["users"]], [account["id"]])
        path = f"/api/admin/users/{account['id']}/actions"
        self.assertEqual(admin.call(path, "POST", {"action": "block"})[0], 200)
        self.assertEqual(user.call("/api/auth/me")[0], 401)
        self.assertEqual(user.call("/api/auth/login", "POST", {"login": "qa_user", "password": "test-password-only-42"})[0], 401)
        self.assertEqual(admin.call(path, "POST", {"action": "unblock"})[0], 200)
        self.assertEqual(user.call("/api/auth/refresh", "POST")[0], 401)
        self.assertEqual(user.call("/api/auth/login", "POST", {"login": "qa_user", "password": "test-password-only-42"})[0], 200)
        self.assertEqual(admin.call(f"/api/admin/users/{admin_account['id']}/actions", "POST", {"action": "block"})[0], 404)

    def test_bad_requests_and_rate_limits_are_explicit(self):
        client = Client(self.base)
        body = {"login": "qa_user", "password": "tiny-secret"}
        self.assertEqual(client.call("/api/auth/register", "POST", body, browser_header=False)[0], 403)
        status, result, _ = client.call("/api/auth/register", "POST", body)
        self.assertEqual(status, 422)
        self.assertNotIn(body["password"], json.dumps(result))
        attempts = [client.call("/api/auth/login", "POST", {"login": "missing", "password": "bad"})[0] for _ in range(13)]
        self.assertEqual(attempts, [401] * 12 + [429])

    def test_empty_dashboard_and_invalid_filters(self):
        client = Client(self.base)
        status, data, _ = client.call("/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(data["dates"], [])
        self.assertIsNone(data["forecast"])
        self.assertEqual(client.call("/api/forecasts")[0], 404)
        self.assertEqual(client.call("/api/dashboard?date=2026-99-99")[0], 422)
        self.assertEqual(client.call("/api/dashboard?date=2026-11-11")[0], 404)
        self.assertEqual(client.call("/api/runs?limit=-1")[0], 422)
        self.assertEqual(len(client.call("/api/zones")[1]), 6)
        self.assertEqual(client.call("/api/experiments")[1], {"experiments": []})
        self.assertEqual(client.call("/api/experiments?limit=0")[0], 422)
        self.database.save_experiment({"experiment_id": "exp-20261113T120000-abcdef", "trials": []})
        self.assertEqual(client.call("/api/experiments")[1]["experiments"][0]["experiment_id"], "exp-20261113T120000-abcdef")

    @unittest.skipUnless(os.getenv("PULT_MINIO_ENDPOINT"), "MinIO endpoint not configured")
    def test_avatar_storage_permissions_and_deletion(self):
        from PIL import Image
        from backend.services.avatars import remove_avatar
        client, account = self.register()
        other, _ = self.register("qa_other")
        self.assertEqual(client.call("/api/auth/avatar", "PUT", raw=b"not an image")[0], 422)
        picture = io.BytesIO()
        Image.new("RGB", (40, 60), "green").save(picture, format="PNG")
        status, profile, _ = client.call("/api/auth/avatar", "PUT", raw=picture.getvalue())
        self.assertEqual(status, 200, profile)
        self.addCleanup(remove_avatar, profile["avatar_id"])
        path = f"/api/auth/avatars/{account['id']}"
        status, content, _ = client.call(path)
        self.assertEqual(status, 200)
        self.assertEqual(Image.open(io.BytesIO(content)).size, (256, 256))
        self.assertEqual(other.call(path)[0], 403)
        self.auth.promote("qa_other")
        self.assertEqual(other.call(path)[0], 200)
        self.assertEqual(client.call("/api/auth/avatar", "DELETE")[0], 200)
        self.assertEqual(client.call(path)[0], 404)
