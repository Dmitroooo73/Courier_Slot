from datetime import date, timedelta
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

from backend.pipeline.forecast import BASELINE, forecast_cutoff, zone_forecasts
from backend.pipeline.orders import SEED, load_history
from backend.pipeline.quality import quality_summary
from backend.pipeline.weather import MODEL as WEATHER_MODEL
from backend.pipeline.weather_archive import LEAD_HOURS, WEATHER_FEATURES, weather_history

from .features import CATEGORIES, LAGS, ORDER_FEATURES, features_for_day


DEPENDENCIES = ("catboost", "pandas", "scikit-learn", "numpy", "scipy")
HISTORY_DAYS = 365
FIRST_MODEL_DATE = date(2025, 4, 1)


def report_inputs(target: date, data: Path, journal: dict):
    history =order_inputs(target, data, HISTORY_DAYS, journal)
    first = min(history) + timedelta(days=max(LAGS))
    weather = weather_inputs(first, target, data, journal)
    journal["data_quality"] = quality_summary(history, weather)
    journal["step"] = "model"
    return training_frame(history, weather, first), feature_frame(target, history, weather[target]), weather


def training_frame(history: dict, weather: dict, first: date):
    """One row per closed day and zone, with the same features as a daily forecast."""
    import pandas as pd

    rows = [
        {**row, "actual_orders": history[day][row["zone_id"]]}
        for day in sorted(history)
        if day >= first
        for row in features_for_day(day, history, weather[day])
    ]
    return pd.DataFrame.from_records(rows).astype(dict.fromkeys(WEATHER_FEATURES, "float64"))


def order_inputs(target: date, data: Path, days: int, journal: dict) -> dict:
    journal["step"] = "orders"
    history, new_days = load_history(target, data / "raw" / "orders", days)
    journal["orders"] = {
        "first_day": min(history).isoformat(),
        "last_day": max(history).isoformat(),
        "generated_days": new_days,
        "reused_days": len(history) - new_days,
        "new_day_source": "synthetic_event_log",
    }
    return history


def weather_inputs(first: date, target: date, data: Path, journal: dict) -> dict:
    journal["step"] = "weather"
    weather, downloaded_days = weather_history(
        first, target, data / "raw" / "weather" / "previous_runs_48h"
    )
    journal["weather"] = {
        "origin": "api" if downloaded_days else "cache",
        "downloaded_utc_days": downloaded_days,
    }
    return weather


def feature_frame(target: date, history: dict, weather: dict):
    import pandas as pd

    return pd.DataFrame.from_records(features_for_day(target, history, weather)).astype(
        dict.fromkeys(WEATHER_FEATURES, "float64"))


def dataset_digest(frame) -> str:
    dataset = frame.to_json(orient="records", date_format="iso")
    return sha256(dataset.encode("utf-8")).hexdigest()


def model_versions() -> dict[str, str]:
    return {dependency: version(dependency) for dependency in DEPENDENCIES}


def model_parameters(half_life: float | None = None) -> dict:
    from .evaluation import CATBOOST_PARAMETERS, RIDGE_PARAMETERS

    weights = {} if half_life is None else {"recency_half_life_days": half_life}
    return {BASELINE: {"lag_days": 7}, "ridge": {**RIDGE_PARAMETERS, **weights},
            "catboost": {**CATBOOST_PARAMETERS, **weights}}


def build_report(target: date, data: Path, journal: dict) -> tuple[dict, tuple]:
    frame, future, weather = report_inputs(target, data, journal)
    return evaluate_report(target, frame, future, weather, journal)


def evaluate_report(target: date, frame, future, weather: dict, journal: dict, *,
                    half_life: float | None = None) -> tuple[dict, tuple]:
    from .evaluation import evaluate

    evaluation, fitted = evaluate(frame, future, half_life=half_life)
    forecasts = zone_forecasts(evaluation.pop("forecast"))
    explanations = evaluation.pop("explanations")
    for forecast in forecasts:
        forecast["explanation"] = explanations[forecast["zone_id"]]
    missing_weather = sum(None in weather[day].values() for day in frame["date"].unique())
    report = {
        "target_date": target.isoformat(),
        "forecast_cutoff": forecast_cutoff(target).isoformat(),
        "orders_source": "synthetic",
        "historical_rows": len(frame),
        "training": {"start": frame["date"].min().isoformat(), "end": frame["date"].max().isoformat(), "rows": len(frame)},
        "dataset_sha256": dataset_digest(frame),
        "seed": SEED,
        "versions": model_versions(),
        "feature_columns": {
            "categorical": list(CATEGORIES),
            "orders": list(ORDER_FEATURES),
            "weather": list(WEATHER_FEATURES),
        },
        "parameters": model_parameters(half_life),
        "data_quality": journal["data_quality"],
        "weather": {
            **forecast_weather(weather[target]),
            "training_days_with_missing_weather": missing_weather,
        },
        **evaluation,
        "forecasts": forecasts,
    }
    return report, fitted


def forecast_weather(values: dict) -> dict:
    return {"provider": "Open-Meteo", "model": WEATHER_MODEL, "lead_hours": LEAD_HOURS, **values}
