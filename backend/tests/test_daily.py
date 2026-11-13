import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.daily import daily_lock, latest_due, publish_daily, watch
from backend.pipeline.orders import MOSCOW
from backend.pipeline.orders import SOURCE
from backend.pipeline.results import FileResults, ModelVersion


def activate_test_version(data: Path, version_id: str = "test-version") -> None:
    record = {"activation_id": "20261107T000000000000Z-00000000", "action": "initialize", "version_id": version_id,
              "previous_version_id": None, "at_utc": "2026-11-07T00:00:00+00:00", "context": {}}
    FileResults(data).activate_model(record, expected=None,
                                     model=ModelVersion({"version_id": version_id, "orders_source": SOURCE}, b"test"))


@patch.dict("os.environ", {"PULT_DATABASE_URL": ""})
class DailyTests(unittest.TestCase):
    def test_target_switches_at_twenty_moscow_time(self):
        before = datetime(2026, 11, 7, 16, 59, 59, tzinfo=timezone.utc)
        at_cutoff = before + timedelta(seconds=1)
        self.assertEqual(latest_due(before), date(2026, 11, 7))
        self.assertEqual(latest_due(at_cutoff), date(2026, 11, 8))

    def test_second_run_cannot_acquire_the_same_lock(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / ".daily.lock"
            with daily_lock(path), self.assertRaisesRegex(RuntimeError, "уже выполняется"):
                with daily_lock(path):
                    self.fail("Lock was acquired twice")
            with daily_lock(path):
                self.assertTrue(path.is_file())

    def test_repeat_skips_published_date_and_failed_refresh_keeps_it(self):
        target = date(2026, 11, 7)
        with (
            TemporaryDirectory(dir=Path.cwd()) as directory,
            redirect_stdout(io.StringIO()),
            patch("backend.daily.forecast_from_saved", return_value={"target_date": target.isoformat(),
                                                                  "model_version_id": "test-version"}) as predict,
            patch("backend.daily.observation_record", return_value=None),
        ):
            data = Path(directory)
            activate_test_version(data)
            publish_daily(target, data)
            output = data / "evaluations" / f"{target}.json"
            first = output.read_bytes()
            self.assertEqual(json.loads(first)["model_version_id"], "test-version")
            self.assertEqual(len(list((data / "model_registry").glob("*.json"))), 1)
            publish_daily(target, data)
            self.assertEqual(predict.call_count, 1)

            predict.side_effect = OSError("Open-Meteo offline")
            with self.assertRaisesRegex(OSError, "Open-Meteo"):
                publish_daily(target, data, force=True)
            self.assertEqual(output.read_bytes(), first)

            logs = [json.loads(path.read_text(encoding="utf-8")) for path in (data / "runs").glob("*.json")]
            self.assertEqual({log["status"] for log in logs}, {"success", "failed"})
            failed = next(log for log in logs if log["status"] == "failed")
            self.assertEqual(failed["last_forecast_file"], f"evaluations/{target}.json")

    def test_clean_stand_trains_first_version_once_and_failure_keeps_previous_report(self):
        from backend.pipeline.results import EVALUATIONS

        target = date(2026, 11, 7)
        with (
            TemporaryDirectory() as directory,
            redirect_stdout(io.StringIO()),
            patch("backend.daily.forecast_from_saved", return_value={"target_date": target.isoformat(),
                                                                  "model_version_id": "test-version"}),
            patch("backend.daily.observation_record", return_value=None),
            patch("backend.daily.train_first_version", side_effect=OSError("Open-Meteo offline")) as first,
        ):
            data = Path(directory)
            store = FileResults(data)
            previous = target - timedelta(days=1)
            store.publish(EVALUATIONS, previous, {"target_date": previous.isoformat()})
            with self.assertRaisesRegex(OSError, "Open-Meteo"):
                publish_daily(target, data)
            self.assertFalse(store.has(EVALUATIONS, target))
            failed, = store.recent_runs(10)
            self.assertEqual((failed["status"], failed["step"]), ("failed", "first_model"))
            self.assertEqual(failed["last_forecast_file"], f"evaluations/{previous}.json")

            first.side_effect = lambda *_args, **_kwargs: activate_test_version(data)
            publish_daily(target, data)
            publish_daily(target + timedelta(days=1), data)
            self.assertEqual(first.call_count, 2)
            self.assertEqual(store.active_model()["version_id"], "test-version")
            self.assertTrue(store.has(EVALUATIONS, target + timedelta(days=1)))

    def test_watcher_catches_up_and_retries_before_next_cutoff(self):
        current = datetime(2026, 11, 7, 20, 2, tzinfo=MOSCOW)
        data = Path("data")
        with (
            patch("backend.daily.datetime") as clock,
            patch("backend.daily.publish_daily", side_effect=OSError("timeout")) as run,
            patch("backend.daily.settle_observations"),
            patch("backend.daily.check_input_drift"),
            patch("backend.daily.maintain_models") as models,
            patch("backend.daily.prepare_data_version"),
            patch("backend.daily.ensure_demo", return_value=None),
            patch("backend.daily.wait_until", side_effect=KeyboardInterrupt) as wait,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            clock.now.return_value = current
            with self.assertRaises(KeyboardInterrupt):
                watch(data)
            run.assert_called_once_with(date(2026, 11, 8), data)
            models.assert_called_once_with(data, current)
            self.assertEqual(wait.call_args.args[0], current + timedelta(minutes=5))
