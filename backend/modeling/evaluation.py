from datetime import date, timedelta
from statistics import fmean

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from backend.pipeline.forecast import BASELINE, seasonal_explanation
from backend.pipeline.orders import SEED
from backend.pipeline.weather_archive import WEATHER_FEATURES

from .explanations import feature_effects, feature_explanations
from .features import CATEGORIES, ORDER_FEATURES
from .scenarios import scenario_curves, scenario_ranges


WINDOW_DAYS = 28
VALIDATION_WINDOWS = 3
MODELS = (
    (BASELINE, False),
    ("ridge", False),
    ("ridge", True),
    ("catboost", False),
    ("catboost", True),
)
RIDGE_PARAMETERS = {"alpha": 1.0}
CATBOOST_PARAMETERS = {
    "iterations": 250,
    "depth": 5,
    "learning_rate": 0.05,
    "random_seed": SEED,
    "has_time": True,
    "one_hot_max_size": 7,
    "thread_count": 2,
    "verbose": False,
    "allow_writing_files": False,
}


def recency_weights(dates: pd.Series, half_life: float) -> np.ndarray:
    """Weight halves every `half_life` days before the newest training date."""
    newest = max(dates)
    age = np.array([(newest - day).days for day in dates], dtype="float64")
    return 0.5 ** (age / half_life)


def fit_model(model: str, uses_weather: bool, train: pd.DataFrame, *, excluded: tuple[str, ...] = (),
              half_life: float | None = None):
    numeric = [feature for feature in ORDER_FEATURES if feature not in excluded]
    if uses_weather:
        numeric.extend(feature for feature in WEATHER_FEATURES if feature not in excluded)
    categories = [feature for feature in CATEGORIES if feature not in excluded]
    columns = [*categories, *numeric]
    if model == "ridge":
        preprocessing = ColumnTransformer(
            [
                ("categorical", OneHotEncoder(handle_unknown="ignore"), categories),
                (
                    "numeric",
                    make_pipeline(
                        SimpleImputer(strategy="median", keep_empty_features=True, add_indicator=True),
                        StandardScaler(),
                    ),
                    numeric,
                ),
            ]
        )
        estimator = make_pipeline(preprocessing, Ridge(**RIDGE_PARAMETERS))
        weight_argument = "ridge__sample_weight"
    else:
        estimator = CatBoostRegressor(cat_features=categories, **CATBOOST_PARAMETERS)
        weight_argument = "sample_weight"
    weights = {} if half_life is None else {weight_argument: recency_weights(train["date"], half_life)}
    estimator.fit(train[columns], train["actual_orders"], **weights)
    return estimator, columns


def order_counts(predictions) -> list[int]:
    return [max(0, round(float(value))) for value in predictions]


def predict(model: str, uses_weather: bool, train: pd.DataFrame, test: pd.DataFrame, *,
            half_life: float | None = None) -> list[int]:
    if model == BASELINE:
        return test["orders_lag_7"].tolist()
    estimator, columns = fit_model(model, uses_weather, train, half_life=half_life)
    return order_counts(estimator.predict(test[columns]))


def predict_explained(
    model: str, uses_weather: bool, train: pd.DataFrame, test: pd.DataFrame
) -> tuple[list[int], list[dict]]:
    counts, explanations, _ = forecast_details(model, uses_weather, train, test)
    return counts, explanations


def forecast_details(
    model: str, uses_weather: bool, train: pd.DataFrame | None, test: pd.DataFrame, *,
    include_scenarios: bool = False, fitted: tuple | None = None, reference: dict | None = None,
) -> tuple[list[int], list[dict], dict[str, list[dict]]]:
    if model == BASELINE:
        columns = ["orders_lag_7"]

        def predict_rows(rows):
            return rows["orders_lag_7"].astype(int).tolist()

        counts = predict_rows(test)
        explanations = [
            seasonal_explanation(count, (day - timedelta(days=7)).isoformat())
            for day, count in zip(test["date"], counts, strict=True)
        ]
    else:
        estimator, columns = fitted if fitted is not None else fit_model(model, uses_weather, train)

        def predict_rows(rows):
            return order_counts(estimator.predict(rows[columns]))

        inputs = test[columns]
        raw = estimator.predict(inputs)
        counts = order_counts(raw)
        training_inputs = train[columns] if train is not None else None
        explanations = feature_explanations(model, estimator, training_inputs, inputs, raw,
                                            center=reference["ridge_center"] if reference is not None else None)
    scenarios = {}
    if include_scenarios:
        ranges = scenario_ranges(train) if reference is None else reference["scenario_ranges"]
        scenarios = scenario_curves(ranges, test, counts, columns, predict_rows)
    return counts, explanations, scenarios


def score(actual: list[int], forecast: list[int]) -> dict:
    errors = [abs(expected - observed) for observed, expected in zip(actual, forecast, strict=True)]
    total = sum(actual)
    return {
        "mae_orders": fmean(errors),
        "wape_percent": 100 * sum(errors) / total if total else None,
        "observations": len(actual),
    }


def split(frame: pd.DataFrame, start: date, end: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    # At 20:00 on the previous day, its final daily demand is still unknown.
    train = frame[frame["date"] < start - timedelta(days=1)]
    test = frame[frame["date"].between(start, end)]
    return train, test


def importance_for_window(model: str, uses_weather: bool, train: pd.DataFrame, valid: pd.DataFrame, *,
                          half_life: float | None = None) -> list[dict]:
    observed = valid["actual_orders"].to_numpy()
    if model == BASELINE:
        feature = "orders_lag_7"
        values = valid[feature].to_numpy()
        rng = np.random.default_rng(SEED)
        original_mae = np.abs(observed - values).mean()
        shuffled_mae = fmean(np.abs(observed - rng.permutation(values)).mean() for _ in range(5))
        return [{
            "feature": feature,
            "mean_abs_orders": float(np.abs(values - train[feature].mean()).mean()),
            "permutation_mae_delta": float(shuffled_mae - original_mae),
        }]

    estimator, columns = fit_model(model, uses_weather, train, half_life=half_life)
    inputs = valid[columns]
    _, effects = feature_effects(model, estimator, train[columns], inputs)
    permutation = permutation_importance(
        estimator, inputs, observed, scoring="neg_mean_absolute_error", n_repeats=5, random_state=SEED
    )
    return [
        {"feature": column, "mean_abs_orders": float(effect), "permutation_mae_delta": float(delta)}
        for column, effect, delta in zip(
            columns, np.abs(effects).mean(axis=0), permutation.importances_mean, strict=True
        )
    ]


def evaluate(frame: pd.DataFrame, future: pd.DataFrame, *, half_life: float | None = None) -> tuple[dict, tuple]:
    dates = sorted(frame["date"].unique())
    development = dates[-WINDOW_DAYS * (VALIDATION_WINDOWS + 1) : -WINDOW_DAYS]
    windows = [
        (development[offset], development[offset + WINDOW_DAYS - 1])
        for offset in range(0, len(development), WINDOW_DAYS)
    ]
    folds = [split(frame, start, end) for start, end in windows]
    comparison = []
    for model, uses_weather in MODELS:
        actual, predictions, details = [], [], []
        by_zone: dict[str, list[tuple[int, int]]] = {}
        for (start, end), (train, valid) in zip(windows, folds, strict=True):
            observed = valid["actual_orders"].tolist()
            predicted = predict(model, uses_weather, train, valid, half_life=half_life)
            actual.extend(observed)
            predictions.extend(predicted)
            for zone, fact, estimate in zip(valid["zone_id"], observed, predicted, strict=True):
                by_zone.setdefault(zone, []).append((fact, estimate))
            details.append(
                {
                    "train_through": train["date"].max().isoformat(),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    **score(observed, predicted),
                }
            )
        comparison.append(
            {
                "model": model,
                "uses_weather": uses_weather,
                **score(actual, predictions),
                "windows": details,
                "by_zone": {
                    zone: score([fact for fact, _ in rows], [estimate for _, estimate in rows])
                    for zone, rows in by_zone.items()
                },
            }
        )

    best = min(comparison, key=lambda row: row["mae_orders"])
    selected = {"model": best["model"], "uses_weather": best["uses_weather"]}
    importance = [
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "features": importance_for_window(**selected, train=train, valid=valid, half_life=half_life),
        }
        for (start, end), (train, valid) in zip(windows, folds, strict=True)
    ]
    start, end = dates[-WINDOW_DAYS], dates[-1]
    train, holdout = split(frame, start, end)
    observed = holdout["actual_orders"].tolist()
    predicted = predict(**selected, train=train, test=holdout, half_life=half_life)
    baseline = predict(BASELINE, False, train, holdout)
    predictions = [
        {"date": day.isoformat(), "zone_id": zone, "actual": fact, "forecast": estimate, "baseline": naive}
        for day, zone, fact, estimate, naive in zip(
            holdout["date"], holdout["zone_id"], observed, predicted, baseline, strict=True
        )
    ]
    fitted = (None, ["orders_lag_7"]) if best["model"] == BASELINE else fit_model(**selected, train=frame, half_life=half_life)
    forecast, explanations, scenarios = forecast_details(
        **selected, train=frame, test=future, include_scenarios=True, fitted=fitted,
    )
    result = {
        "validation": comparison,
        "feature_importance": importance,
        "scenarios": scenarios,
        "selected": selected,
        "holdout": {
            "train_through": train["date"].max().isoformat(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "selected": score(observed, predicted),
            "baseline": score(observed, baseline),
            "predictions": predictions,
        },
        "forecast": dict(zip(future["zone_id"], forecast, strict=True)),
        "explanations": dict(zip(future["zone_id"], explanations, strict=True)),
    }
    return result, fitted
