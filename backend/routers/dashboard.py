from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.pipeline.orders import ZONES
from backend.pipeline.results import result_store
from backend.services.dashboard import available_dates, read_forecast, recent_runs


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
    dates = available_dates(directory)
    forecast, metrics = (None, None)
    if target is not None or dates:
        forecast, metrics = get_result(directory, target or dates[0])
    return {
        "dates": dates,
        "forecast": forecast,
        "metrics": metrics,
        "runs": recent_runs(directory),
        "observations": result_store(directory).observations(),
    }


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
