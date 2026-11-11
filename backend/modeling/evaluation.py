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
from .scenarios import scenario_curves


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


def fit_model(model: str, uses_weather: bool, train: pd.DataFrame):
    numeric = list(ORDER_FEATURES)
    if uses_weather:
        numeric.extend(WEATHER_FEATURES)
    columns = [*CATEGORIES, *numeric]
    if model == "ridge":
        preprocessing = ColumnTransformer(
            [
                ("categorical", OneHotEncoder(handle_unknown="ignore"), list(CATEGORIES)),
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
    else:
        estimator = CatBoostRegressor(cat_features=list(CATEGORIES), **CATBOOST_PARAMETERS)
    estimator.fit(train[columns], train["actual_orders"])
    return estimator, columns


def order_counts(predictions) -> list[int]:
    return [max(0, round(float(value))) for value in predictions]


def predict(model: str, uses_weather: bool, train: pd.DataFrame, test: pd.DataFrame) -> list[int]:
    if model == BASELINE:
        return test["orders_lag_7"].tolist()
    estimator, columns = fit_model(model, uses_weather, train)
    return order_counts(estimator.predict(test[columns]))


def predict_explained(
    model: str, uses_weather: bool, train: pd.DataFrame, test: pd.DataFrame
) -> tuple[list[int], list[dict]]:
    counts, explanations, _ = forecast_details(model, uses_weather, train, test)
    return counts, explanations


def forecast_details(
    model: str, uses_weather: bool, train: pd.DataFrame, test: pd.DataFrame, *, include_scenarios: bool = False
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
        estimator, columns = fit_model(model, uses_weather, train)

        def predict_rows(rows):
            return order_counts(estimator.predict(rows[columns]))

        inputs = test[columns]
        raw = estimator.predict(inputs)
        counts = order_counts(raw)
        explanations = feature_explanations(model, estimator, train[columns], inputs, raw)
    scenarios = scenario_curves(train, test, counts, columns, predict_rows) if include_scenarios else {}
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


def importance_for_window(model: str, uses_weather: bool, train: pd.DataFrame, valid: pd.DataFrame) -> list[dict]:
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

    estimator, columns = fit_model(model, uses_weather, train)
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


def evaluate(frame: pd.DataFrame, future: pd.DataFrame) -> dict:
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
            predicted = predict(model, uses_weather, train, valid)
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
            "features": importance_for_window(**selected, train=train, valid=valid),
        }
        for (start, end), (train, valid) in zip(windows, folds, strict=True)
    ]
    start, end = dates[-WINDOW_DAYS], dates[-1]
    train, holdout = split(frame, start, end)
    observed = holdout["actual_orders"].tolist()
    predicted = predict(**selected, train=train, test=holdout)
    baseline = predict(BASELINE, False, train, holdout)
    predictions = [
        {"date": day.isoformat(), "zone_id": zone, "actual": fact, "forecast": estimate, "baseline": naive}
        for day, zone, fact, estimate, naive in zip(
            holdout["date"], holdout["zone_id"], observed, predicted, baseline, strict=True
        )
    ]
    forecast, explanations, scenarios = forecast_details(**selected, train=frame, test=future, include_scenarios=True)
    return {
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
