import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.pipeline.observations import collect_closed_days, observation_record
from unittest.mock import patch

from backend.pipeline.orders import LEGACY_SOURCE, MOSCOW, SOURCE, SOURCE_MARKER, ZONES
from backend.pipeline.results import EVALUATIONS, FileResults


class ObservationTests(unittest.TestCase):
    def test_closed_day_compares_with_original_forecast_and_is_collected_once(self):
        target = date(2026, 11, 8)
        before = datetime(2026, 11, 7, 20, tzinfo=MOSCOW)
        during = datetime(2026, 11, 8, 23, 59, tzinfo=MOSCOW)
        after = datetime(2026, 11, 9, 0, tzinfo=MOSCOW)
        selected = {"model": "ridge", "uses_weather": True}
        report = {
            "selected": selected,
            "validation": [{**selected, "by_zone": {zone.id: {"mae_orders": 5} for zone in ZONES}}],
            "forecasts": [{"zone_id": zone.id, "forecast_orders": 100} for zone in ZONES],
            "orders_source": "synthetic",
        }
        record = observation_record(target, report, before)
        self.assertIsNone(observation_record(target, report, during))
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            data = Path(directory)
            store = FileResults(data)
            store.publish(EVALUATIONS, target, report, observation=record)
            store.publish(EVALUATIONS, target, report, observation={**record, "forecasts": {"east": 900}})
            self.assertEqual(collect_closed_days(store, data, during), 0)
            self.assertIsNone(store.observations()[0]["actuals"])
            self.assertEqual(collect_closed_days(store, data, after), 1)
            observed = store.observations()[0]
            self.assertEqual(collect_closed_days(store, data, after), 0)
            self.assertEqual(store.observations(), [observed])
            # An archived forecast is compared with facts of its own data version.
            self.assertTrue((data / "raw" / "archive" / LEGACY_SOURCE / "orders" / f"{target}.csv").is_file())
            self.assertFalse((data / "raw" / "orders" / f"{target}.csv").exists())

            from backend.tests.weather_stub import observed_stub

            current = date(2026, 11, 9)
            fresh = observation_record(current, {**report, "orders_source": SOURCE}, datetime(2026, 11, 8, 20, tzinfo=MOSCOW))
            store.publish(EVALUATIONS, current, report, observation=fresh)
            with patch("backend.pipeline.weather_archive.observed_weather", observed_stub):
                self.assertEqual(collect_closed_days(store, data, datetime(2026, 11, 10, 0, 10, tzinfo=MOSCOW)), 1)
            self.assertTrue((data / "raw" / "orders" / f"{current}.csv").is_file())
            self.assertTrue((data / "raw" / "orders" / SOURCE_MARKER).is_file())
        self.assertEqual(observed["forecasts"]["east"], 100)
        self.assertEqual(set(observed["actuals"]), {zone.id for zone in ZONES})
