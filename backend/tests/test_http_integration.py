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
