import json
import unittest
from datetime import date, timedelta
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.pipeline.forecast import BASELINE
from backend.pipeline.storage import write_json
from backend.services.dashboard import read_forecast


class LegacyExplanationTests(unittest.TestCase):
    def test_baseline_report_explains_its_reference_date(self):
        target = date(2026, 10, 24)
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            data = Path(directory)
            write_json(data / "forecasts" / f"{target}.json", {
                "model": BASELINE,
                "reference_date": "2026-10-17",
                "forecasts": [{"zone_id": "east", "forecast_orders": 133}],
            })
            forecast, _ = read_forecast(data, target)
        explanation = forecast["forecasts"][0]["explanation"]
        self.assertEqual(explanation["reference_date"], "2026-10-17")
        self.assertEqual(explanation["raw_orders"], 133)


@unittest.skipUnless(find_spec("catboost"), "requires the dependencies in requirements-ml.txt")
class ModelExplanationTests(unittest.TestCase):
    def test_explanation_reconstructs_the_published_forecast_with_missing_weather(self):
        import pandas as pd

        from backend.modeling.evaluation import predict, predict_explained

        start = date(2026, 10, 1)
        train = pd.DataFrame([
            {
                "date": start + timedelta(days=offset),
                "zone_id": zone,
                "weekday": str(offset % 7),
                "orders_lag_2": 95 + offset,
                "orders_lag_7": 105 - offset,
                "orders_lag_14": 100 + offset % 3,
                "mean_closed_week": 95 + offset / 3,
                "mean_temperature_c": None if offset % 3 == 0 else 7 + offset / 2,
                "precipitation_mm": float(offset % 4),
                "hours_with_precipitation": float(offset % 3),
                "actual_orders": 110 + offset + (15 if zone == "east" else 0),
            }
            for offset in range(21)
            for zone in ("east", "north")
        ])
        future = train.iloc[[0, -1]].drop(columns="actual_orders")
        for model, method in (("ridge", "ridge"), ("catboost", "catboost_shap"), (BASELINE, BASELINE)):
            with self.subTest(model=model):
                counts, explanations = predict_explained(model, model != BASELINE, train, future)
                self.assertEqual(counts, predict(model, model != BASELINE, train, future))
                for count, explanation in zip(counts, explanations, strict=True):
                    self.assertEqual(explanation["method"], method)
                    total = explanation["base_orders"] + sum(
                        row["impact_orders"] for row in explanation["contributions"]
                    )
                    self.assertAlmostEqual(total, explanation["raw_orders"], places=7)
                    self.assertEqual(max(0, round(explanation["raw_orders"])), count)
                if model != BASELINE:
                    values = {row["feature"]: row["value"] for row in explanations[0]["contributions"]}
                    self.assertIsNone(values["mean_temperature_c"])
                    self.assertEqual(values["zone_id"], "east")
                json.dumps(explanations, allow_nan=False)
