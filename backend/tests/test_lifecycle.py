import io
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.pipeline.results import FileResults, ModelVersion


NOW = datetime(2026, 3, 8, 1, 0, tzinfo=timezone.utc)


def activate(store: FileResults, version_id: str, training_end: str = "2026-02-18") -> None:
    store.save_model(ModelVersion({"version_id": version_id, "training": {"end": training_end}}, b"artifact"))
    record = {"activation_id": f"20260101T000000000000Z-{len(store.activations(100)):08d}", "action": "initialize",
              "version_id": version_id, "previous_version_id": None, "candidate_id": None, "actor": "test",
              "reason": "", "at_utc": NOW.isoformat(), "context": {}}
    assert store.activate_model(record, expected=None)


def alert(zone: str, end: date) -> dict:
    points = [{"date": (end - timedelta(days=offset)).isoformat(), "actual": 150, "forecast": 110, "reference": 6}
              for offset in range(13, -1, -1)]
    return {"zone_id": zone, "end_date": end.isoformat(), "start_date": points[0]["date"], "rule_version": "weekly-error-v1",
            "status": "alert", "action": "retrain_recommended", "points": points}


def observe(store: FileResults, end: date, version_id: str) -> None:
    for offset in range(14):
        day = end - timedelta(days=offset)
        store.save_observation(day, {"target_date": day.isoformat(), "model_version_id": version_id, "actuals": {}})


@patch.dict("os.environ", {"PULT_DATABASE_URL": ""})
class LifecycleRuleTests(unittest.TestCase):
    def test_promotion_needs_five_percent_gain_and_beating_last_week(self):
        from backend.modeling.lifecycle import decide

        def scores(candidate, baseline=20.0):
            return {"active": {"mae_orders": 10.0}, "candidate": {"mae_orders": candidate}, "baseline": {"mae_orders": baseline}}

        self.assertTrue(decide(scores(9.5))["accepted"])
        rejected = decide(scores(9.51))
        self.assertFalse(rejected["accepted"])
        self.assertIn("меньше требуемых", rejected["summary"])
        self.assertFalse(decide(scores(9.0, baseline=8.9))["accepted"])

    def test_clean_window_starts_two_days_after_training(self):
        from backend.modeling.lifecycle import clean_window_issue

        self.assertIsNone(clean_window_issue("2026-11-04", date(2026, 11, 12)))
        issue = clean_window_issue("2026-11-05", date(2026, 11, 12))
        self.assertIn("2026-11-13", issue)

    def test_retrain_signal_queues_one_job_from_working_version_evidence(self):
        from backend.modeling.lifecycle import monitoring_trigger, request_candidate

        end = date(2026, 3, 7)
        with TemporaryDirectory() as directory:
            store = FileResults(Path(directory))
            self.assertIsNone(monitoring_trigger(store, NOW))
            activate(store, "v1")
            observe(store, end, "v0")
            store.save_decisions([alert("east", end)])
            self.assertIsNone(monitoring_trigger(store, NOW), "alert about an older version")

            observe(store, end, "v1")
            trigger = monitoring_trigger(store, NOW)
            self.assertEqual((trigger["zones"], trigger["end_date"]), (["east"], end.isoformat()))
            job, created = request_candidate(store, trigger, NOW)
            self.assertTrue(created)
            self.assertEqual(request_candidate(store, trigger, NOW), (job, False))
            manual = {"kind": "manual", "end_date": end.isoformat(), "zones": [], "requested_by": "admin:qa"}
            self.assertEqual(request_candidate(store, manual, NOW), (job, False), "one open job at a time")

            store.save_candidate({**job, "status": "failed", "error": "offline"})
            retried, created = request_candidate(store, trigger, NOW)
            self.assertEqual((retried["candidate_id"], retried["status"], retried["attempts"], created),
                             (job["candidate_id"], "queued", 2, True))

            store.save_candidate({**retried, "status": "rejected"})
            next_day = end + timedelta(days=1)
            observe(store, next_day, "v1")
            store.save_decisions([alert("east", next_day)])
            self.assertIsNone(monitoring_trigger(store, NOW), "cooldown after a candidate for this version")
            self.assertEqual(len(store.candidates()), 1)


def no_weather(first, last, _data, journal):
    journal["weather"] = {"origin": "test", "downloaded_utc_days": 0}
    return {first + timedelta(days=offset): dict.fromkeys(("mean_temperature_c", "precipitation_mm", "hours_with_precipitation"))
            for offset in range((last - first).days + 1)}


@patch.dict("os.environ", {"PULT_DATABASE_URL": ""})
@patch("backend.modeling.report.weather_inputs", no_weather)
@patch("backend.modeling.lifecycle.weather_inputs", no_weather)
@patch.dict("backend.modeling.evaluation.CATBOOST_PARAMETERS", {"iterations": 20})
class LifecycleCycleTests(unittest.TestCase):
    def test_shift_promotes_candidate_and_rollback_restores_previous_version(self):
        from backend.modeling.demo_cycle import shift_zone
        from backend.modeling.lifecycle import LifecycleConflict, request_candidate, rollback, run_candidate, train_first_version
        from backend.pipeline.orders import load_history

        start = date(2026, 2, 20)
        shifted = date(2026, 2, 22)
        end = shifted + timedelta(days=13)
        with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            data = Path(directory)
            store = FileResults(data)
            orders = data / "raw" / "orders"
            load_history(end + timedelta(days=2), orders, 380)
            first = train_first_version(store, data, start, NOW, actor="test")
            self.assertEqual(store.model_metadata(first)["training"]["end"], "2026-02-18")
            shift_zone(orders, [shifted + timedelta(days=offset) for offset in range(14)], "east", 1.35)

            trigger = {"kind": "manual", "end_date": end.isoformat(), "zones": [], "requested_by": "admin:qa"}
            later = NOW + timedelta(hours=1)
            job, created = request_candidate(store, trigger, later)
            self.assertTrue(created)
            result = run_candidate(store, data, job, later)
            self.assertEqual(result["status"], "activated", result.get("decision") or result.get("error"))
            scores = result["comparison"]["scores"]
            self.assertLess(scores["candidate"]["mae_orders"], 0.95 * scores["active"]["mae_orders"])
            self.assertEqual(result["training"]["end"], "2026-02-27")
            self.assertEqual({point["date"] for point in result["comparison"]["points"]},
                             {(end - timedelta(days=offset)).isoformat() for offset in range(7)})
            candidate = result["version_id"]
            self.assertEqual(store.model_metadata(candidate)["status"], "candidate")
            self.assertEqual(store.model_metadata(candidate)["parameters"]["recency_half_life_days"], 14)
            active = store.active_model()
            self.assertEqual(active["version_id"], candidate)
            self.assertIn("promotion", active)
            self.assertEqual(request_candidate(store, trigger, NOW), (result, False))

            with self.assertRaises(LifecycleConflict):
                rollback(store, expected=first, actor="admin:qa", now=NOW)
            record = rollback(store, expected=candidate, actor="admin:qa", now=NOW + timedelta(hours=2))
            self.assertEqual((record["action"], record["version_id"], record["previous_version_id"]),
                             ("rollback", first, candidate))
            self.assertEqual(store.active_model()["version_id"], first)
            self.assertEqual([item["action"] for item in store.activations()], ["rollback", "activate", "initialize"])
            self.assertEqual(store.active_model()["evaluation"]["training"]["end"], "2026-02-18")
