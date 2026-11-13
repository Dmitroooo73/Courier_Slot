import unittest
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.daily import settle_observations
from backend.pipeline.monitoring import RULE_VERSION, decision_records, persist_decisions
from backend.pipeline.orders import MOSCOW
from backend.pipeline.results import EVALUATIONS, FileResults


NOW = datetime(2026, 11, 13, 2, tzinfo=MOSCOW)


def observations(errors):
    records = []
    for offset, error in enumerate(errors):
        target = date(2026, 10, 1) + timedelta(days=offset)
        records.append({"target_date": target.isoformat(), "model": "ridge", "uses_weather": True,
                        "orders_source": "synthetic", "forecasts": {"east": 100},
                        "actuals": {"east": 100 + error}, "validation_mae": {"east": 10},
                        "published_at_utc": f"{target - timedelta(days=1)}T17:00:00+00:00",
                        "observed_at_utc": f"{target}T21:00:00+00:00"})
    return records


class MonitoringTests(unittest.TestCase):
    def test_complete_weeks_escalate_and_recovery_clears_the_signal(self):
        records = list(decision_records(observations([20] * 14 + [2] * 14), NOW))
        self.assertEqual(records[5]["status"], "pending")
        self.assertEqual(records[6]["status"], "warning")
        self.assertEqual(records[13]["status"], "alert")
        self.assertEqual(records[13]["action"], "retrain_recommended")
        self.assertEqual(records[20]["status"], "warning")
        self.assertEqual(records[27]["status"], "normal")
        self.assertEqual(records[27]["start_date"], "2026-10-15")

    def test_limit_equality_is_not_an_alert_and_each_week_has_its_reference(self):
        source = observations([13] * 7)
        result = list(decision_records(source, NOW))[-1]
        self.assertEqual(result["status"], "normal")
        self.assertEqual(result["weeks"][0]["limit"], 13)
        source += observations([20] * 14)[7:]
        for record in source[7:]:
            record["validation_mae"]["east"] = 20
        result = list(decision_records(source, NOW))[-1]
        self.assertEqual([week["limit"] for week in result["weeks"]], [13, 26])
        self.assertEqual(result["status"], "normal")

    def test_gap_or_invalid_input_restarts_contiguous_history(self):
        source = observations([20] * 14)
        result = list(decision_records(source[:7] + source[8:], NOW))[-1]
        self.assertEqual(result["status"], "pending")
        self.assertEqual(len(result["points"]), 6)
        source[8]["actuals"]["east"] = float("nan")
        result = list(decision_records(source, NOW))[-1]
        self.assertEqual(len(result["points"]), 5)
        self.assertEqual(result["status"], "pending")

    def test_unclosed_days_and_future_changes_never_rewrite_earlier_results(self):
        source = observations([20] * 14)
        earlier = list(decision_records(source[:7], NOW))
        source[-1]["actuals"]["east"] = 9000
        self.assertEqual(list(decision_records(source, NOW))[:7], earlier)
        source[6]["actuals"] = None
        self.assertEqual(len(list(decision_records(source, datetime(2026, 10, 8, tzinfo=MOSCOW)))), 6)

    def test_file_history_is_immutable_idempotent_and_paged(self):
        with TemporaryDirectory() as directory:
            store = FileResults(Path(directory))
            for record in observations([20] * 14):
                store.save_observation(date.fromisoformat(record["target_date"]), record)
            self.assertEqual(persist_decisions(store, NOW), 14)
            first = store.decisions(RULE_VERSION, "east", limit=5)
            self.assertEqual(persist_decisions(store, NOW + timedelta(days=1)), 0)
            self.assertEqual(store.decisions(RULE_VERSION, "east", limit=5), first)
            self.assertEqual(FileResults(Path(directory)).latest_decisions(RULE_VERSION), first[:1])
            second = store.decisions(RULE_VERSION, "east", before=date.fromisoformat(first[-1]["end_date"]), limit=5)
            self.assertTrue(set(row["end_date"] for row in first).isdisjoint(row["end_date"] for row in second))
            self.assertEqual(store.decisions(RULE_VERSION, "north"), [])
            altered = deepcopy(first[0])
            altered["status"] = "normal"
            self.assertEqual(store.save_decisions([altered]), 0)
            self.assertEqual(store.latest_decisions(RULE_VERSION), first[:1])

    @patch.dict("os.environ", {"PULT_DATABASE_URL": ""})
    def test_retry_recovers_decision_after_actuals_were_saved_before_a_failure(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            store = FileResults(data)
            record = {**observations([20])[0], "actuals": None, "observed_at_utc": None}
            store.publish(EVALUATIONS, date(2026, 10, 1), {}, observation=record)
            with patch.object(FileResults, "save_decisions", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    settle_observations(data, NOW)
            self.assertIsNotNone(store.observations()[0]["actuals"])
            self.assertEqual(store.latest_decisions(RULE_VERSION), [])
            settle_observations(data, NOW)
            self.assertEqual(len(store.latest_decisions(RULE_VERSION)), 1)
