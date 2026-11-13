from datetime import date
from pathlib import Path

from backend.pipeline.forecast import BASELINE, seasonal_explanation
from backend.pipeline.results import EVALUATIONS, FORECASTS, InvalidArtifact, read_artifact, result_store


def available_dates(directory: Path) -> list[date]:
    return result_store(directory).dates()


def read_forecast(directory: Path, target: date) -> tuple[dict, dict | None]:
    name = f"{target}.json"
    store = result_store(directory)
    try:
        report = store.read(EVALUATIONS, target)
    except FileNotFoundError:
        forecast = store.read(FORECASTS, target)
        if forecast.get("model") == BASELINE and "reference_date" in forecast:
            try:
                for zone in forecast["forecasts"]:
                    zone["explanation"] = seasonal_explanation(
                        zone["forecast_orders"], forecast["reference_date"]
                    )
            except (KeyError, TypeError) as error:
                raise InvalidArtifact(name) from error
        return forecast, None

    try:
        forecast = {
            "target_date": report["target_date"],
            "forecast_cutoff": report["forecast_cutoff"],
            "orders_source": report["orders_source"],
            "model": report["selected"]["model"],
            "uses_weather": report["selected"]["uses_weather"],
            "weather": report["weather"],
            "forecasts": report["forecasts"],
        }
        if "model_version_id" in report:
            forecast["model_version_id"] = report["model_version_id"]
        if "forecast_mode" in report:
            forecast["forecast_mode"] = report["forecast_mode"]
        if "published_at_utc" in report:
            forecast["published_at_utc"] = report["published_at_utc"]
        metrics = {
            "validation": report["validation"],
            "selected": report["selected"],
            "holdout": report["holdout"],
        }
        if "feature_importance" in report:
            metrics["feature_importance"] = report["feature_importance"]
        if "scenarios" in report:
            metrics["scenarios"] = report["scenarios"]
        if "data_quality" in report:
            metrics["data_quality"] = report["data_quality"]
        if "promotion" in report:
            metrics["promotion"] = report["promotion"]
    except (KeyError, TypeError) as error:
        raise InvalidArtifact(name) from error
    return forecast, metrics


def recent_runs(directory: Path, limit: int = 5) -> list[dict]:
    return result_store(directory).recent_runs(limit)
