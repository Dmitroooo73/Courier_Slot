import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.modeling.features import ORDER_FEATURES, ORDER_INPUT_LAGS
from backend.pipeline.drift import RULE_VERSION, compare_series, dates_between, load_days, persist_input_drift
from backend.pipeline.orders import MOSCOW, ZONES
from backend.pipeline.results import EVALUATIONS, FileResults


NOW = datetime(2026, 11, 13, 0, 1, tzinfo=MOSCOW)


def sample_days(_data: Path, start: date, end: date) -> list[dict]:
    return [
        {"date": day.isoformat(),
         "orders": {zone.id: dict.fromkeys(ORDER_FEATURES, 100 + 10 * day.weekday()) for zone in ZONES},
         "weather": {"mean_temperature_c": 8, "precipitation_mm": 0, "hours_with_precipitation": 0}}
        for day in dates_between(start, end)
    ]


class InputDriftTests(unittest.TestCase):
    def test_detects_distribution_change_with_the_same_median(self):
        reference = [(0, 10)] * 56
        current = [(0, 0)] * 7 + [(0, 20)] * 7
        report = compare_series("mean_temperature_c", None, reference, current, fixed=True)
        self.assertEqual(report["reference_median"], report["current_median"])
        self.assertEqual((report["status"], report["distance"]), ("shift", 0.5))

    def test_weekday_adjustment_and_insufficient_weekday_coverage(self):
        reference = [(day, 100 + 10 * day) for day in range(7) for _ in range(8)]
        current = [(day, 100 + 10 * day) for day in range(7) for _ in range(1 if day < 4 else 2)]
        report = compare_series("orders_lag_2", "east", reference, current, fixed=True)
        self.assertEqual((report["status"], report["distance"]), ("steady", 0))
        report = compare_series("orders_lag_2", "east", reference, current[1:], fixed=True)
        self.assertEqual((report["status"], report["current_count"], report["distance"]), ("insufficient", 9, None))
        # The count alone is sufficient here, but Mondays are still absent.
        report = compare_series("orders_lag_2", "east", reference, current[1:] + [current[-1]], fixed=True)
        self.assertEqual((report["status"], report["current_count"]), ("insufficient", 10))

    def test_missing_inputs_stay_missing_and_open_days_are_not_read(self):
        end = date(2026, 11, 12)
        with TemporaryDirectory() as directory, patch("backend.pipeline.weather_archive.download") as download:
            data = Path(directory)
            orders = data / "raw" / "orders"
            orders.mkdir(parents=True)
            for lag in ORDER_INPUT_LAGS:
                day = end - timedelta(days=lag)
                rows = [f"{day}-{zone.id},{day}T08:00:00+03:00,{zone.id}\n" for zone in ZONES]
                (orders / f"{day}.csv").write_text("order_id,created_at,zone_id\n" + "".join(rows), encoding="utf-8")
            for day in (end - timedelta(days=1), end, end + timedelta(days=1)):
                (orders / f"{day}.csv").write_text("invalid,do not read", encoding="utf-8")
            row, = load_days(data, end, end)
            self.assertEqual(row["orders"]["east"], dict.fromkeys(ORDER_FEATURES, 1))
            self.assertTrue(all(value is None for value in row["weather"].values()))
            (orders / f"{end - timedelta(days=7)}.csv").unlink()
            row, = load_days(data, end, end)
            self.assertEqual(row["orders"], {})
            download.assert_not_called()

    def test_frozen_reference_and_original_forecast_survive_next_check(self):
        with TemporaryDirectory() as directory, patch("backend.pipeline.drift.load_days", side_effect=sample_days) as load:
            data = Path(directory)
            store = FileResults(data)
            target = NOW.date()
            forecast = {"target_date": target.isoformat()}
            store.publish(EVALUATIONS, target, forecast)
            first = persist_input_drift(store, data, NOW)
            reference = store.drift_reference(RULE_VERSION)
            self.assertTrue(first["reference_fixed"])
            self.assertEqual(first["current"]["end_date"], "2026-11-12")
            self.assertLess(first["reference"]["end_date"], first["current"]["start_date"])
            self.assertEqual(first["orders"]["east"][0]["status"], "steady")
            self.assertIsNone(persist_input_drift(FileResults(data), data, NOW))
            self.assertEqual(load.call_count, 1)

            def shifted(data, start, end):
                rows = sample_days(data, start, end)
                for row in rows:
                    row["orders"]["east"]["orders_lag_2"] += 60
                    row["weather"]["precipitation_mm"] = None
                return rows

            load.side_effect = shifted
            second = persist_input_drift(FileResults(data), data, NOW + timedelta(days=1))
            self.assertEqual(second["reference"], first["reference"])
            self.assertEqual(second["orders"]["east"][0]["status"], "shift")
            self.assertEqual(second["orders"]["north"][0]["status"], "steady")
            self.assertEqual(second["weather"][1]["status"], "insufficient")
            self.assertEqual(second["weather"][1]["current_count"], 0)
            self.assertEqual(store.drift_reference(RULE_VERSION), reference)
            self.assertEqual(store.latest_drift(RULE_VERSION), second)
            self.assertEqual(store.read(EVALUATIONS, target), forecast)

    def test_incomplete_reference_is_not_frozen(self):
        def partial(data, start, end):
            rows = sample_days(data, start, end)
            for row in rows[:20]:
                row["weather"]["mean_temperature_c"] = None
            return rows

        with TemporaryDirectory() as directory, patch("backend.pipeline.drift.load_days", side_effect=partial):
            data = Path(directory)
            store = FileResults(data)
            report = persist_input_drift(store, data, NOW)
            self.assertFalse(report["reference_fixed"])
            self.assertEqual(report["weather"][0]["reference_count"], 36)
            self.assertIsNone(store.drift_reference(RULE_VERSION))
            self.assertEqual(store.latest_drift(RULE_VERSION), report)
