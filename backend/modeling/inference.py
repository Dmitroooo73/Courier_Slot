"""Daily prediction from an already fitted estimator."""
from datetime import date, timedelta
from pathlib import Path

from backend.pipeline.forecast import forecast_cutoff, zone_forecasts
from backend.pipeline.results import ModelVersion

from .features import ORDER_INPUT_LAGS
from .registry import load_estimator
from .report import feature_frame, forecast_weather, order_inputs, weather_inputs


def forecast_from_saved(target: date, data: Path, journal: dict, saved: ModelVersion, active: dict) -> dict:
    from .evaluation import forecast_details

    metadata = saved.metadata
    if metadata["training"]["end"] > (target - timedelta(days=2)).isoformat():
        raise ValueError("Рабочая модель обучена на данных после отсечки этой даты.")
    estimator = load_estimator(saved)
    history = order_inputs(target, data, max(ORDER_INPUT_LAGS) - 1, journal)
    weather = weather_inputs(target, target, data, journal)[target]
    future = feature_frame(target, history, weather)
    journal["step"] = "predict"
    selected = {key: metadata[key] for key in ("model", "uses_weather")}
    counts, explanations, scenarios = forecast_details(
        **selected, train=None, test=future, include_scenarios=True,
        fitted=(estimator, metadata["features"]), reference=active["reference"],
    )
    forecasts = zone_forecasts(dict(zip(future["zone_id"], counts, strict=True)))
    for forecast, explanation in zip(forecasts, explanations, strict=True):
        forecast["explanation"] = explanation
    return {
        **active["evaluation"],
        "target_date": target.isoformat(),
        "forecast_cutoff": forecast_cutoff(target).isoformat(),
        "orders_source": metadata["orders_source"],
        "model_version_id": metadata["version_id"],
        "forecast_mode": "saved_model",
        "selected": selected,
        "weather": forecast_weather(weather),
        "forecasts": forecasts,
        "scenarios": scenarios,
        **({"promotion": active["promotion"]} if "promotion" in active else {}),
    }
