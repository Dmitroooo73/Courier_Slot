import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.pipeline.orders import MOSCOW
from backend.pipeline.results import FileResults


def at(hour: int, minute: int = 0, day: int = 13) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=MOSCOW)


@patch.dict("os.environ", {"PULT_DATABASE_URL": ""})
class ScheduleTests(unittest.TestCase):
    def test_late_forecast_and_silent_worker_are_reported(self):
        from backend.routers.dashboard import schedule

        with TemporaryDirectory() as directory:
            store = FileResults(Path(directory))
            today, tomorrow = date(2026, 11, 13), date(2026, 11, 14)
            silent = schedule(store, [today], now=at(18))
            self.assertEqual((silent["expected_forecast"], silent["forecast_late"], silent["worker_silent"]),
                             ("2026-11-13", False, True))

            store.beat({"at_utc": at(20, 25).astimezone(timezone.utc).isoformat(), "state": "waiting"})
            self.assertFalse(schedule(store, [today], now=at(20, 29))["forecast_late"], "still within 30 minutes")
            late = schedule(store, [today], now=at(20, 31))
            self.assertEqual((late["expected_forecast"], late["forecast_late"], late["worker_silent"]),
                             ("2026-11-14", True, False))
            self.assertFalse(schedule(store, [tomorrow, today], now=at(20, 31))["forecast_late"])
            self.assertTrue(schedule(store, [tomorrow], now=at(20, 36))["worker_silent"], "no heartbeat for 11 minutes")
            self.assertTrue(schedule(store, [], now=at(21))["forecast_late"])

    def test_container_healthcheck_follows_the_heartbeat(self):
        from backend import healthcheck

        with TemporaryDirectory() as directory, patch.dict("os.environ", {"PULT_DATA_DIR": directory}):
            store = FileResults(Path(directory))
            self.assertEqual(healthcheck.main(), 1)
            now = datetime.now(timezone.utc)
            store.beat({"at_utc": now.isoformat(), "state": "working"})
            self.assertEqual(healthcheck.main(), 0)
            store.beat({"at_utc": (now - timedelta(minutes=11)).isoformat(), "state": "waiting"})
            self.assertEqual(healthcheck.main(), 1)
