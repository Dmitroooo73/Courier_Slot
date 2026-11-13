from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.pipeline.drift import RULE_VERSION as DRIFT_RULE_VERSION
from backend.pipeline.monitoring import RULE_VERSION
from backend.pipeline.orders import ZONES
from backend.pipeline.results import result_store
from backend.services.dashboard import available_dates, read_forecast, recent_runs


router = APIRouter(prefix="/api", tags=["Пульт доставки"])


def data_directory(request: Request) -> Path:
    return request.app.state.data_directory


DataDirectory = Annotated[Path, Depends(data_directory)]
TargetDate = Annotated[date | None, Query(alias="date")]
ModelCursor = Annotated[str | None, Query(pattern=r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")]


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
    }


@router.get("/models")
def model_registry(directory: DataDirectory, before: ModelCursor = None,
                   limit: Annotated[int, Query(ge=1, le=30)] = 8) -> dict:
    records = result_store(directory).model_versions(limit + 1, before)
    return {"versions": records[:limit],
            "next_before": records[limit - 1]["version_id"] if len(records) > limit else None}


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
