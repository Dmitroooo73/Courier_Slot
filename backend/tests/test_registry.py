import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.pipeline.results import FileResults


class RegistryTests(unittest.TestCase):
    def test_registration_keeps_the_fitted_estimator_that_made_the_forecast(self):
        import pandas as pd

        from backend.modeling.evaluation import BASELINE, fit_model, forecast_details, order_counts
        from backend.modeling.registry import load_estimator, make_model_version
        from backend.modeling.report import dataset_digest, model_parameters

        first = date(2026, 9, 1)
        training = pd.DataFrame([
            {"date": first + timedelta(days=i), "zone_id": zone, "weekday": str(i % 7),
             "orders_lag_2": 92 + i, "orders_lag_7": 95 + i % 7, "orders_lag_14": 90 + i,
             "mean_closed_week": 95 + i / 4, "mean_temperature_c": None if i % 3 == 0 else i / 2,
             "precipitation_mm": float(i % 4), "hours_with_precipitation": float(i % 2),
             "actual_orders": 100 + i * 2 + (10 if zone == "east" else 0)}
            for i in range(28) for zone in ("east", "north")
        ])
        future = training.iloc[[-2, -1]].drop(columns="actual_orders").assign(date=first + timedelta(days=29))
        scores = {"mae_orders": 2.5, "wape_percent": 3.5, "observations": 14}
        with TemporaryDirectory() as directory:
            store = FileResults(Path(directory))
            for model, weather in ((BASELINE, False), ("ridge", True), ("catboost", True)):
                with self.subTest(model=model):
                    selected = {"model": model, "uses_weather": weather}
                    fitted = (None, ["orders_lag_7"]) if model == BASELINE else fit_model(**selected, train=training)
                    with patch("backend.modeling.evaluation.fit_model", side_effect=AssertionError("unexpected fit")):
                        counts, _, _ = forecast_details(**selected, train=training, test=future, fitted=fitted)
                    report = {"selected": selected, "target_date": future["date"].iloc[0].isoformat(),
                              "training": {"start": first.isoformat(), "end": training["date"].max().isoformat(), "rows": len(training)},
                              "dataset_sha256": dataset_digest(training), "orders_source": "synthetic", "seed": 42,
                              "parameters": model_parameters(), "validation": [{**selected, **scores, "windows": []}],
                              "holdout": {"start": "2026-09-22", "end": "2026-09-28", "train_through": "2026-09-20",
                                          "selected": scores, "baseline": scores}}
                    saved = make_model_version(report, *fitted, published=True)
                    store.save_model(saved)
                    loaded = store.model_version(saved.metadata["version_id"])
                    self.assertEqual(loaded.metadata, saved.metadata)
                    self.assertEqual(loaded.metadata["features"], fitted[1])
                    estimator = load_estimator(loaded)
                    predicted = future["orders_lag_7"].tolist() if estimator is None else order_counts(estimator.predict(future[fitted[1]]))
                    self.assertEqual(predicted, counts)
                    with self.assertRaisesRegex(ValueError, "Контрольная сумма"):
                        load_estimator(replace(loaded, artifact=b"changed"))
                    if estimator is not None:
                        incompatible = replace(loaded, metadata={**loaded.metadata, "versions": {"python": "other"}})
                        with self.assertRaisesRegex(ValueError, "несовместима"):
                            load_estimator(incompatible)
