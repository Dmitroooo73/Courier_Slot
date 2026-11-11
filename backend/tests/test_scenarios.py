import json
import unittest
from datetime import date, timedelta
from importlib.util import find_spec


@unittest.skipUnless(find_spec("catboost"), "requires requirements-ml.txt")
class ScenarioTests(unittest.TestCase):
    def test_scenarios_reproduce_the_model_and_keep_other_inputs_fixed(self):
        import pandas as pd

        from backend.modeling.evaluation import BASELINE, forecast_details, predict

        start = date(2026, 9, 1)
        frame = pd.DataFrame([
            {
                "date": start + timedelta(days=day), "zone_id": zone, "weekday": str(day % 7),
                "orders_lag_2": 90 + day, "orders_lag_7": 100 + day % 7,
                "orders_lag_14": 85 + day, "mean_closed_week": 92 + day / 2,
                "mean_temperature_c": float(day - 10), "precipitation_mm": float(day % 3),
                "hours_with_precipitation": float(day % 2),
                "actual_orders": 110 + day * 2 + (20 if zone == "east" else 0),
            }
            for day in range(28) for zone in ("east", "north")
        ])
        future = frame.iloc[[-2, -1]].drop(columns="actual_orders").copy()
        future_before = future.copy(deep=True)
        train_before = frame.copy(deep=True)
        for model, weather in ((BASELINE, False), ("ridge", False), ("ridge", True), ("catboost", True)):
            with self.subTest(model=model, weather=weather):
                counts, _, scenarios = forecast_details(model, weather, frame, future, include_scenarios=True)
                self.assertEqual(counts, predict(model, weather, frame, future))
                for position, (_, row) in enumerate(future.iterrows()):
                    for curve in scenarios[row["zone_id"]]:
                        original = next(point for point in curve["points"] if point["value"] == curve["base_value"])
                        self.assertEqual(original["orders"], counts[position])
                        point = curve["points"][0]
                        changed = future.iloc[[position]].assign(**{curve["feature"]: point["value"]})
                        self.assertEqual(point["orders"], predict(model, weather, frame, changed)[0])
                        if not curve["used"]:
                            self.assertEqual({point["orders"] for point in curve["points"]}, {counts[position]})
                json.dumps(scenarios, allow_nan=False)
        pd.testing.assert_frame_equal(frame, train_before)
        pd.testing.assert_frame_equal(future, future_before)

    def test_values_include_the_published_value_and_handle_missing_history(self):
        import pandas as pd

        from backend.modeling.scenarios import scenario_values

        self.assertIn(4.125, scenario_values(pd.Series([-2.0, 20.0]), 4.125))
        self.assertEqual(scenario_values(pd.Series([float("nan")]), None), [])
        self.assertEqual(scenario_values(pd.Series([float("nan")]), 3.5), [3.5])
