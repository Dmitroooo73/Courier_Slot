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
    import pandas as pd

    journal["step"] = "orders"
    history, new_days = load_history(target, data / "raw" / "orders", HISTORY_DAYS)
    journal["orders"] = {
        "first_day": min(history).isoformat(),
        "last_day": max(history).isoformat(),
        "generated_days": new_days,
        "reused_days": len(history) - new_days,
        "new_day_source": "synthetic_event_log",
    }
    first = min(history) + timedelta(days=max(LAGS))
    journal["step"] = "weather"
    weather, downloaded_days = weather_history(
        first, target, data / "raw" / "weather" / "previous_runs_48h"
    )
    journal["weather"] = {
        "origin": "api" if downloaded_days else "cache",
        "downloaded_utc_days": downloaded_days,
    }
    journal["data_quality"] = quality_summary(history, weather)
    journal["step"] = "model"
    rows = [
        {**row, "actual_orders": history[day][row["zone_id"]]}
        for day in sorted(history)
        if day >= first
        for row in features_for_day(day, history, weather[day])
    ]
    numeric_weather = dict.fromkeys(WEATHER_FEATURES, "float64")
    frame = pd.DataFrame.from_records(rows).astype(numeric_weather)
    future_rows = features_for_day(target, history, weather[target])
    future = pd.DataFrame.from_records(future_rows).astype(numeric_weather)
    return frame, future, weather


def dataset_digest(frame) -> str:
    dataset = frame.to_json(orient="records", date_format="iso")
    return sha256(dataset.encode("utf-8")).hexdigest()


def model_versions() -> dict[str, str]:
    return {dependency: version(dependency) for dependency in DEPENDENCIES}


def model_parameters() -> dict:
    from .evaluation import CATBOOST_PARAMETERS, RIDGE_PARAMETERS

    return {BASELINE: {"lag_days": 7}, "ridge": RIDGE_PARAMETERS, "catboost": CATBOOST_PARAMETERS}


def build_report(target: date, data: Path, journal: dict) -> dict:
    from .evaluation import evaluate

    frame, future, weather = report_inputs(target, data, journal)
    evaluation = evaluate(frame, future)
    forecasts = zone_forecasts(evaluation.pop("forecast"))
    explanations = evaluation.pop("explanations")
    for forecast in forecasts:
        forecast["explanation"] = explanations[forecast["zone_id"]]
    missing_weather = sum(None in weather[day].values() for day in frame["date"].unique())
    return {
        "target_date": target.isoformat(),
        "forecast_cutoff": forecast_cutoff(target).isoformat(),
        "orders_source": "synthetic",
        "historical_rows": len(frame),
        "dataset_sha256": dataset_digest(frame),
        "seed": SEED,
        "versions": model_versions(),
        "feature_columns": {
            "categorical": list(CATEGORIES),
            "orders": list(ORDER_FEATURES),
            "weather": list(WEATHER_FEATURES),
        },
        "parameters": model_parameters(),
        "data_quality": journal["data_quality"],
        "weather": {
            "provider": "Open-Meteo",
            "model": WEATHER_MODEL,
            "lead_hours": LEAD_HOURS,
            "training_days_with_missing_weather": missing_weather,
            **weather[target],
        },
        **evaluation,
        "forecasts": forecasts,
    }
