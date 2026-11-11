import csv
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.pipeline.orders import generate_event_log, import_event_log, synthetic_records


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
                original + original.splitlines()[1] + "\n",
            ]
            event = original.splitlines()[1]
            variants.append(original.replace(event, event + " zone=east", 1))
            variants.append(original.replace(" INFO export.complete", " INFO interrupted") +
                            event.replace("zone=center", "zone=east") + "\n")
            for invalid in variants:
                with self.subTest(invalid=variants.index(invalid)):
                    source.write_text(invalid, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        import_event_log(day, source, csv_path)
                    self.assertEqual(csv_path.read_bytes(), saved)
