from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.pipeline.drift import RULE_VERSION as DRIFT_RULE_VERSION
from backend.pipeline.monitoring import RULE_VERSION
from backend.pipeline.orders import ZONES
from backend.pipeline.results import result_store
from backend.services.dashboard import available_dates, read_forecast, recent_runs
from backend.services.weather_now import WeatherUnavailable, current_weather

LATE_AFTER = timedelta(minutes=30)
WORKER_SILENT_AFTER = timedelta(minutes=10)


def schedule(store, dates: list, now: datetime | None = None) -> dict:
    """Is tonight's forecast out and is the worker alive? The page shows a warning when not."""
    from backend.daily import latest_due
    from backend.pipeline.forecast import forecast_cutoff
    from backend.pipeline.orders import MOSCOW

    now = (now or datetime.now(timezone.utc)).astimezone(MOSCOW)
    expected = latest_due(now)
    latest = dates[0] if dates else None
    beat = store.heartbeat()
    seen = datetime.fromisoformat(beat["at_utc"]) if beat else None
    return {"expected_forecast": expected.isoformat(), "latest_forecast": latest.isoformat() if latest else None,
            "forecast_late": (latest is None or latest < expected) and now >= forecast_cutoff(expected) + LATE_AFTER,
            "worker_seen_utc": beat["at_utc"] if beat else None,
            "worker_silent": seen is None or now - seen > WORKER_SILENT_AFTER}


router = APIRouter(prefix="/api", tags=["Пульт доставки"])


def data_directory(request: Request) -> Path:
    return request.app.state.data_directory


DataDirectory = Annotated[Path, Depends(data_directory)]
TargetDate = Annotated[date | None, Query(alias="date")]


def get_result(directory: Path, target: date | None) -> tuple[dict, dict | None]:
    if target is None:
        dates = available_dates(directory)
        target = dates[0] if dates else None
    if target is not None:
        try:
            return read_forecast(directory, target)
        except FileNotFoundError as error:
            raise HTTPException(404, "На эту дату нет сохранённого прогноза.") from error
    raise HTTPException(404, "Сначала запустите расчёт прогноза.")


@router.get("/dashboard")
def dashboard(directory: DataDirectory, target: TargetDate = None) -> dict:
    store = result_store(directory)
    dates = store.dates()
    forecast, metrics = (None, None)
    if target is not None or dates:
        forecast, metrics = get_result(directory, target or dates[0])
    forecast_model = None
    if forecast and (version_id := forecast.get("model_version_id")):
        try:
            forecast_model = store.model_metadata(version_id)
        except FileNotFoundError:
            pass
    return {
        "dates": dates,
        "forecast": forecast,
        "metrics": metrics,
        "runs": recent_runs(directory),
        "observations": store.observations(),
        "monitoring_decisions": store.latest_decisions(RULE_VERSION),
        "input_drift": store.latest_drift(DRIFT_RULE_VERSION),
        "forecast_model": forecast_model,
        "schedule": schedule(store, dates),
    }


@router.get("/monitoring/decisions")
def monitoring_decisions(directory: DataDirectory, zone: str,
                         before: date | None = None,
                         limit: Annotated[int, Query(ge=1, le=30)] = 10) -> dict:
    if zone not in {item.id for item in ZONES}:
        raise HTTPException(422, "Выберите существующую зону доставки.")
    records = result_store(directory).decisions(RULE_VERSION, zone, limit + 1, before)
    return {"decisions": records[:limit],
            "next_before": records[limit - 1]["end_date"] if len(records) > limit else None}


@router.get("/zones")
def zones() -> list[dict]:
    return [
        {"zone_id": zone.id, "zone_name": zone.name, "capacity_orders": zone.capacity}
        for zone in ZONES
    ]


@router.get("/forecasts")
def forecasts(directory: DataDirectory, target: TargetDate = None) -> dict:
    forecast, _ = get_result(directory, target)
    return forecast


@router.get("/metrics")
def metrics(directory: DataDirectory, target: TargetDate = None) -> dict | None:
    _, evaluation = get_result(directory, target)
    return evaluation


@router.get("/runs")
def runs(directory: DataDirectory, limit: Annotated[int, Query(ge=1, le=20)] = 5) -> list[dict]:
    return recent_runs(directory, limit)


@router.get("/weather/now")
def weather_now() -> dict:
    """Current weather in Moscow for the dispatcher. The model uses archived forecast runs instead."""
    try:
        return current_weather()
    except WeatherUnavailable as error:
        raise HTTPException(503, str(error)) from error
