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
            patch("backend.daily.build_report", return_value={"target_date": target.isoformat()}) as build,
            patch("backend.daily.observation_record", return_value=None),
        ):
            data = Path(directory)
            publish_daily(target, data)
            output = data / "evaluations" / f"{target}.json"
            first = output.read_bytes()
            publish_daily(target, data)
            self.assertEqual(build.call_count, 1)

            build.side_effect = OSError("Open-Meteo offline")
            with self.assertRaisesRegex(OSError, "Open-Meteo"):
                publish_daily(target, data, force=True)
            self.assertEqual(output.read_bytes(), first)

            logs = [json.loads(path.read_text(encoding="utf-8")) for path in (data / "runs").glob("*.json")]
            self.assertEqual({log["status"] for log in logs}, {"success", "failed"})
            failed = next(log for log in logs if log["status"] == "failed")
            self.assertEqual(failed["last_forecast_file"], f"evaluations/{target}.json")

    def test_watcher_catches_up_and_retries_before_next_cutoff(self):
        current = datetime(2026, 11, 7, 20, 2, tzinfo=MOSCOW)
        data = Path("data")
        with (
            patch("backend.daily.datetime") as clock,
            patch("backend.daily.publish_daily", side_effect=OSError("timeout")) as run,
            patch("backend.daily.settle_observations"),
            patch("backend.daily.wait_until", side_effect=KeyboardInterrupt) as wait,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            clock.now.return_value = current
            with self.assertRaises(KeyboardInterrupt):
                watch(data)
            run.assert_called_once_with(date(2026, 11, 8), data)
            wait.assert_called_once_with(current + timedelta(minutes=5))
