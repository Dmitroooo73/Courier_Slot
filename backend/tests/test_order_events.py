import csv
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.pipeline.orders import (
    LEGACY_SOURCE, SOURCE, SOURCE_MARKER, ensure_layout, generate_event_log, import_event_log, synthetic_records,
    weather_factor,
)


def first_order_event(log: str) -> str:
    return next(line for line in log.splitlines() if "order.created" in line)


class EventSourceTests(unittest.TestCase):
    def test_free_text_and_redeliveries_preserve_original_daily_orders(self):
        day = date(2026, 11, 11)
        with TemporaryDirectory() as directory:
            source, csv_path = Path(directory) / "events.log", Path(directory) / "orders.csv"
            generate_event_log(day, source)
            stats = import_event_log(day, source, csv_path)
            with csv_path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.reader(stream))[1:]
            self.assertEqual(rows, [list(record) for record in synthetic_records(day)])
            self.assertGreater(stats["duplicate_events"], 0)
            self.assertGreater(stats["ignored_lines"], 0)
            initial = csv_path.read_bytes()
            self.assertEqual(import_event_log(day, source, csv_path), stats)
            self.assertEqual(csv_path.read_bytes(), initial)

    def test_bad_or_incomplete_events_do_not_replace_previous_csv(self):
        day = date(2026, 11, 11)
        with TemporaryDirectory() as directory:
            source, csv_path = Path(directory) / "events.log", Path(directory) / "orders.csv"
            generate_event_log(day, source)
            original = source.read_text(encoding="utf-8")
            import_event_log(day, source, csv_path)
            saved = csv_path.read_bytes()
            variants = [
                original.replace("zone=center", "zone=unknown", 1),
                original.replace("order_id=", "missing_id=", 1),
                original.replace(" INFO export.complete", " INFO interrupted"),
                original + first_order_event(original) + "\n",
            ]
            event = first_order_event(original)
            variants.append(original.replace(event, event + " zone=east", 1))
            variants.append(original.replace(" INFO export.complete", " INFO interrupted") +
                            event.replace("zone=center", "zone=east") + "\n")
            for invalid in variants:
                with self.subTest(invalid=variants.index(invalid)):
                    source.write_text(invalid, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        import_event_log(day, source, csv_path)
                    self.assertEqual(csv_path.read_bytes(), saved)

    def test_rain_and_cold_raise_demand_only_in_the_weather_version(self):
        day = date(2026, 11, 11)
        rainy_cold = {"mean_temperature_c": -2.0, "precipitation_mm": 6.0, "hours_with_precipitation": 10}
        self.assertAlmostEqual(weather_factor(rainy_cold), 1 + 0.015 * (10 - 4) + 0.006 * (10 - 5))
        self.assertAlmostEqual(weather_factor({"mean_temperature_c": 20.0, "hours_with_precipitation": 0}), 0.91)
        self.assertAlmostEqual(weather_factor({"mean_temperature_c": 3.0, "hours_with_precipitation": 4}), 1.0)
        self.assertAlmostEqual(weather_factor({"mean_temperature_c": None, "hours_with_precipitation": 30}), 1 + 0.015 * 8)
        calm = len(list(synthetic_records(day, {"mean_temperature_c": 20.0, "hours_with_precipitation": 0})))
        self.assertGreater(len(list(synthetic_records(day, rainy_cold))), calm * 1.15)
        legacy = list(synthetic_records(day, rainy_cold, source=LEGACY_SOURCE))
        self.assertEqual(legacy, list(synthetic_records(day, source=LEGACY_SOURCE)), "the archived version ignores weather")
        with TemporaryDirectory() as directory:
            log = Path(directory) / "events.log"
            generate_event_log(day, log, rainy_cold)
            self.assertIn("weather.observed mean_temperature_c=-2.0 hours_with_precipitation=10 demand_factor=1.1200",
                          log.read_text(encoding="utf-8"))
            generate_event_log(day, log, source=LEGACY_SOURCE)
            self.assertNotIn("weather.observed", log.read_text(encoding="utf-8"))

    def test_older_version_moves_to_archive_once(self):
        with TemporaryDirectory() as directory:
            raw = Path(directory) / "raw"
            (raw / "orders").mkdir(parents=True)
            (raw / "order_events").mkdir()
            (raw / "orders" / "2026-11-01.csv").write_text("v1", encoding="utf-8")
            (raw / "order_events" / "2026-11-01.log").write_text("log", encoding="utf-8")
            ensure_layout(raw / "orders")
            archive = raw / "archive" / LEGACY_SOURCE
            self.assertEqual((archive / "orders" / "2026-11-01.csv").read_text(encoding="utf-8"), "v1")
            self.assertTrue((archive / "order_events" / "2026-11-01.log").is_file())
            self.assertEqual([path.name for path in (raw / "orders").iterdir()], [SOURCE_MARKER])
            self.assertEqual((raw / "orders" / SOURCE_MARKER).read_text(encoding="utf-8").strip(), SOURCE)
            (raw / "orders" / "2026-11-02.csv").write_text("v2", encoding="utf-8")
            ensure_layout(raw / "orders")
            self.assertTrue((raw / "orders" / "2026-11-02.csv").is_file(), "the current version stays in place")
