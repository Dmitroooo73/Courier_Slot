import unittest
from datetime import date, timedelta

from backend.pipeline.orders import ZONES
from backend.pipeline.quality import quality_summary


class QualityTests(unittest.TestCase):
    def test_future_values_do_not_change_earlier_anomaly_decisions(self):
        start = date(2026, 1, 1)
        history = {start + timedelta(days=i): {zone.id: 100 for zone in ZONES} for i in range(70)}
        history[start + timedelta(days=40)]["east"] = 1000
        before = quality_summary(history, {start: {"temperature": None}})
        self.assertEqual(before["anomaly_count"], 1)
        self.assertEqual(before["anomaly_examples"][0]["usual_orders"], 100)
        self.assertEqual(before["missing_weather_days"], 1)
        history[start + timedelta(days=75)] = {zone.id: 3000 for zone in ZONES}
        after = quality_summary(history, {})
        self.assertEqual(after["anomaly_examples"][0], before["anomaly_examples"][0])
        self.assertEqual(history[start + timedelta(days=40)]["east"], 1000)
