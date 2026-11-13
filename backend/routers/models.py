"""Model registry and lifecycle. Everyone can read; only administrators act."""
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from backend.modeling.demo_cycle import DEMO_VERSION
from backend.modeling.lifecycle import (
    CONTROL_DAYS, HALF_LIFE_DAYS, MIN_IMPROVEMENT, RULES, LifecycleConflict, clean_window_issue,
    request_candidate, rollback,
)
from backend.pipeline.orders import MOSCOW
from backend.pipeline.results import FileResults, PostgresResults, result_store

from .auth import NO_STORE, CurrentAccount, require_admin, same_origin


VERSION_PATTERN = r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$"
router = APIRouter(prefix="/api", tags=["Модели"])
ModelCursor = Annotated[str | None, Query(pattern=VERSION_PATTERN)]


class RollbackRequest(BaseModel):
    expected_version_id: Annotated[str, Field(pattern=VERSION_PATTERN)]


def last_closed_day(now: datetime) -> date:
    return now.astimezone(MOSCOW).date() - timedelta(days=1)


def metadata_or_none(store: FileResults | PostgresResults, version_id: str | None) -> dict | None:
    if not version_id:
        return None
    try:
        return store.model_metadata(version_id)
    except FileNotFoundError:
        return None


def lifecycle(store: FileResults | PostgresResults, active: dict | None) -> dict:
    activations = store.activations(10)
    current = activations[0] if activations else None
    rollback_to = None
    if active and current and current["version_id"] == active["version_id"]:
        rollback_to = metadata_or_none(store, current["previous_version_id"])
    active_metadata = metadata_or_none(store, active and active["version_id"])
    control_end = last_closed_day(datetime.now(timezone.utc))
    training_end = (active_metadata or {}).get("training", {}).get("end")
    if active_metadata is None:
        issue = "Рабочей модели пока нет — она появится после первого вечернего расчёта."
    elif training_end is None:
        issue = "В паспорте рабочей версии не указан период обучения."
    else:
        issue = clean_window_issue(training_end, control_end)
    return {
        "rules": {**RULES, "control_days": CONTROL_DAYS, "half_life_days": HALF_LIFE_DAYS,
                  "min_improvement_percent": round(100 * MIN_IMPROVEMENT)},
        "active_since": active.get("activated_at_utc") if active else None,
        "candidates": store.candidates(5),
        "activations": activations,
        "rollback_to": rollback_to,
        "manual_check": {"control_end": control_end.isoformat(), "issue": issue},
    }


@router.get("/models")
def model_registry(request: Request, before: ModelCursor = None,
                   limit: Annotated[int, Query(ge=1, le=30)] = 8) -> dict:
    store = result_store(request.app.state.data_directory)
    records = store.model_versions(limit + 1, before)
    active = store.active_model()
    page = {"versions": records[:limit],
            "active_version": metadata_or_none(store, active and active["version_id"]),
            "next_before": records[limit - 1]["version_id"] if len(records) > limit else None}
    if before is None:
        page["lifecycle"] = lifecycle(store, active)
    return page


@router.get("/lifecycle-demo")
def lifecycle_demo(request: Request) -> dict:
    """The replay on isolated, shifted training data. It never touches working versions or forecasts."""
    return {"demo": result_store(request.app.state.data_directory).lifecycle_demo(DEMO_VERSION)}


def database(request: Request) -> PostgresResults:
    store = result_store(request.app.state.data_directory)
    if not isinstance(store, PostgresResults):
        raise HTTPException(503, "Управление моделью доступно при подключённом PostgreSQL.", headers=NO_STORE)
    return store


Database = Annotated[PostgresResults, Depends(database)]
admin = [Depends(require_admin), *same_origin]


@router.post("/admin/models/candidates", dependencies=admin)
def queue_candidate(response: Response, account: CurrentAccount, store: Database) -> dict:
    """Ask the worker to train and check a new version on the last closed week."""
    response.headers.update(NO_STORE)
    now = datetime.now(timezone.utc)
    active = store.active_model()
    if active is None:
        raise HTTPException(409, "Рабочей модели пока нет — она появится после первого вечернего расчёта.", headers=NO_STORE)
    end = last_closed_day(now)
    training_end = store.model_metadata(active["version_id"]).get("training", {}).get("end")
    issue = "В паспорте рабочей версии не указан период обучения." if training_end is None else clean_window_issue(training_end, end)
    if issue is not None:
        raise HTTPException(409, issue, headers=NO_STORE)
    trigger = {"kind": "manual", "end_date": end.isoformat(), "zones": [], "requested_by": f"admin:{account.login}"}
    job, created = request_candidate(store, trigger, now)
    response.status_code = 202 if created else 200
    return {"candidate": job, "created": created}


@router.post("/admin/models/rollback", dependencies=admin)
def rollback_model(data: RollbackRequest, response: Response, account: CurrentAccount, store: Database) -> dict:
    response.headers.update(NO_STORE)
    try:
        with store.daily_lock():
            record = rollback(store, expected=data.expected_version_id, actor=f"admin:{account.login}",
                              now=datetime.now(timezone.utc))
    except LifecycleConflict as error:
        raise HTTPException(409, str(error), headers=NO_STORE) from error
    except ValueError as error:
        raise HTTPException(409, str(error), headers=NO_STORE) from error
    except RuntimeError as error:
        raise HTTPException(409, "Сейчас идёт расчёт или проверка модели. Повторите через минуту.",
                            headers=NO_STORE) from error
    return {"activation": record}
