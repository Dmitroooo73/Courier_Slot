"""Working model lifecycle: first version, candidate, fair comparison, activation and rollback.

Callers hold the daily lock. The rules are described in docs/model-lifecycle.md; changing
any of them requires a new rule version so that saved decisions keep their meaning.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from backend.pipeline.forecast import BASELINE
from backend.pipeline.monitoring import RULE_VERSION as MONITORING_RULE
from backend.pipeline.orders import ZONES
from backend.pipeline.quality import quality_summary
from backend.pipeline.results import FileResults, ModelVersion, PostgresResults

from .features import LAGS
from .registry import load_estimator, make_model_version
from .report import HISTORY_DAYS, evaluate_report, order_inputs, report_inputs, training_frame, weather_inputs


RETRAIN_RULE = "retrain-v1"
CANDIDATE_RULE = "candidate-v1"
PROMOTION_RULE = "promotion-v1"
RULES = {"retrain": RETRAIN_RULE, "candidate": CANDIDATE_RULE, "promotion": PROMOTION_RULE}
CONTROL_DAYS = 7
HALF_LIFE_DAYS = 14
MIN_IMPROVEMENT = 0.05
COOLDOWN_DAYS = 7
DAY = timedelta(days=1)

Store = FileResults | PostgresResults


class LifecycleConflict(RuntimeError):
    """The working version changed while the action was being prepared."""


def record_id(now: datetime) -> str:
    return f"{now.astimezone(timezone.utc):%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}"


def active_context(model: ModelVersion, estimator, frame, report: dict) -> dict:
    """Everything a daily forecast needs besides the artifact: explanation centre, scenario ranges, checks."""
    from .explanations import ridge_center
    from .scenarios import scenario_ranges

    metadata = model.metadata
    return {
        "reference": {
            "ridge_center": ridge_center(estimator, frame[metadata["features"]]).tolist() if metadata["model"] == "ridge" else None,
            "scenario_ranges": scenario_ranges(frame),
        },
        "evaluation": {
            **{key: report[key] for key in ("validation", "holdout", "feature_importance", "dataset_sha256",
                                           "parameters", "versions", "seed", "feature_columns")},
            "training": metadata["training"],
        },
    }


def activation_record(version_id: str, context: dict, *, action: str, previous: str | None, actor: str,
                      reason: str, now: datetime, candidate_id: str | None = None) -> dict:
    return {"activation_id": record_id(now), "action": action, "version_id": version_id,
            "previous_version_id": previous, "candidate_id": candidate_id, "actor": actor, "reason": reason,
            "at_utc": now.astimezone(timezone.utc).isoformat(), "context": context}


def train_first_version(store: Store, data: Path, target: date, now: datetime, actor: str) -> str:
    """A new stand trains its first working version with the standard procedure."""
    journal: dict = {}
    frame, future, weather = report_inputs(target, data, journal)
    report, fitted = evaluate_report(target, frame, future, weather, journal)
    model = make_model_version(report, *fitted, status="registered")
    record = activation_record(model.metadata["version_id"], active_context(model, fitted[0], frame, report),
                               action="initialize", previous=None, actor=actor, now=now,
                               reason="Первая рабочая версия обучена стандартной процедурой.")
    if not store.activate_model(record, expected=None, model=model):
        raise LifecycleConflict("Рабочая модель уже настроена.")
    return model.metadata["version_id"]


def adopt_version(store: Store, model: ModelVersion, context: dict, now: datetime, actor: str) -> str:
    record = activation_record(model.metadata["version_id"], context, action="initialize", previous=None,
                               actor=actor, now=now, reason="Рабочей назначена проверенная версия из реестра.")
    if not store.activate_model(record, expected=None):
        raise LifecycleConflict("Рабочая модель уже настроена.")
    return model.metadata["version_id"]


def ensure_active(store: Store, data: Path, target: date, now: datetime) -> dict:
    """Train the first working version once, so a clean stand starts with a single command."""
    if (active := store.active_model()) is None:
        print("Рабочей модели нет — обучаем первую версию.", flush=True)
        train_first_version(store, data, target, now, actor="worker")
        active = store.active_model()
    return active


def clean_window_issue(training_end: str, end: date) -> str | None:
    """A version can be judged only on days after its last training day plus the cutoff gap."""
    start = end - (CONTROL_DAYS - 1) * DAY
    last_allowed = start - 2 * DAY
    if date.fromisoformat(training_end) <= last_allowed:
        return None
    ready = date.fromisoformat(training_end) + (CONTROL_DAYS + 1) * DAY
    return (f"Рабочая версия обучена по {ru_date(date.fromisoformat(training_end))}. Честная контрольная неделя "
            f"появится после закрытия {ru_date(ready)} ({ready.isoformat()}).")


MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря")


def ru_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]}"


def new_job(trigger: dict, active: dict, now: datetime) -> dict:
    return {"candidate_id": record_id(now), "trigger_key": f"{trigger['kind']}:{trigger['end_date']}",
            "status": "queued", "rules": RULES, "trigger": trigger, "active_version_id": active["version_id"],
            "created_at_utc": now.astimezone(timezone.utc).isoformat(), "attempts": 1}


def request_candidate(store: Store, trigger: dict, now: datetime) -> tuple[dict, bool]:
    """Queue one job per trigger; a repeated request returns the saved job instead of a duplicate."""
    active = store.active_model()
    if active is None:
        raise ValueError("Сначала нужна рабочая модель.")
    job, created = store.queue_candidate(new_job(trigger, active, now))
    if not created and job["trigger_key"] == f"{trigger['kind']}:{trigger['end_date']}" and job["status"] == "failed":
        job = {**job, "status": "queued", "attempts": job.get("attempts", 1) + 1, "error": None,
               "active_version_id": active["version_id"]}
        store.save_candidate(job)
        created = True
    return job, created


def monitoring_trigger(store: Store, now: datetime) -> dict | None:
    """retrain-v1: an `alert` whose 14 days were all forecast by the working version, once per cooldown."""
    active = store.active_model()
    if active is None:
        return None
    alerts = [record for record in store.latest_decisions(MONITORING_RULE) if record["status"] == "alert"]
    if not alerts:
        return None
    end = max(record["end_date"] for record in alerts)
    observations = {record["target_date"]: record for record in store.observations(limit=None)}
    zones = sorted(
        record["zone_id"] for record in alerts
        if record["end_date"] == end and all(
            observations.get(point["date"], {}).get("model_version_id") == active["version_id"]
            for point in record["points"])
    )
    if not zones:
        return None
    since = (date.fromisoformat(end) - COOLDOWN_DAYS * DAY).isoformat()
    # A failed attempt (for example, no network) does not start the pause; the job is retried.
    if any(job["active_version_id"] == active["version_id"] and job["trigger"]["kind"] == "monitoring"
           and job["status"] != "failed" and job["trigger"]["end_date"] > since for job in store.candidates(limit=50)):
        return None
    return {"kind": "monitoring", "end_date": end, "zones": zones, "monitoring_rule": MONITORING_RULE,
            "requested_by": "worker"}


def predictions(metadata: dict, estimator, rows) -> list[int]:
    from .evaluation import order_counts

    if metadata["model"] == BASELINE:
        return rows["orders_lag_7"].astype(int).tolist()
    return order_counts(estimator.predict(rows[metadata["features"]]))


def compare(control, versions: dict[str, tuple[dict, object]]) -> dict:
    """Score every version on the same closed days and zones."""
    from .evaluation import score

    actual = control["actual_orders"].astype(int).tolist()
    series = {name: predictions(metadata, estimator, control) for name, (metadata, estimator) in versions.items()}
    series["baseline"] = control["orders_lag_7"].astype(int).tolist()
    zones = control["zone_id"].tolist()
    result = {}
    for name, values in series.items():
        by_zone = {}
        for zone in ZONES:
            pairs = [(fact, value) for fact, value, owner in zip(actual, values, zones, strict=True) if owner == zone.id]
            by_zone[zone.id] = score([fact for fact, _ in pairs], [value for _, value in pairs])
        result[name] = {**score(actual, values), "by_zone": by_zone}
    points = [{"date": day.isoformat(), "zone_id": zone, "actual": fact,
               **{name: values[index] for name, values in series.items()}}
              for index, (day, zone, fact) in enumerate(zip(control["date"], zones, actual, strict=True))]
    return {"scores": result, "points": points}


def decide(scores: dict) -> dict:
    """promotion-v1: at least 5% smaller MAE than the working version and not worse than last week's orders."""
    active, candidate, baseline = (scores[name]["mae_orders"] for name in ("active", "candidate", "baseline"))
    limit = active * (1 - MIN_IMPROVEMENT)
    gain = 100 * (1 - candidate / active) if active else 0.0
    required = round(100 * MIN_IMPROVEMENT)
    checks = [
        {"id": "improvement", "passed": candidate <= limit,
         "text": f"Средний промах {ru(candidate)} против {ru(active)} у рабочей версии; нужно не больше {ru(limit)}."},
        {"id": "baseline", "passed": candidate <= baseline,
         "text": f"Не хуже прогноза по прошлой неделе: {ru(candidate)} против {ru(baseline)}."},
    ]
    accepted = all(check["passed"] for check in checks)
    if accepted:
        summary = f"Новая версия точнее на {ru(gain, 1)}% — переводим её в работу."
    elif gain <= 0:
        summary = f"Новая версия не точнее рабочей: промах больше на {ru(-gain, 1)}%. Оставляем рабочую."
    elif not checks[0]["passed"]:
        summary = f"Выигрыш {ru(gain, 1)}% меньше требуемых {required}%. Оставляем рабочую версию."
    else:
        summary = "Новая версия хуже прогноза по прошлой неделе. Оставляем рабочую."
    return {"accepted": accepted, "improvement_percent": gain, "checks": checks, "summary": summary}


def ru(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def finish(job: dict, now: datetime, **values) -> dict:
    return {**job, **values, "finished_at_utc": now.astimezone(timezone.utc).isoformat()}


def train_candidate(store: Store, data: Path, job: dict, now: datetime) -> dict:
    end = date.fromisoformat(job["trigger"]["end_date"])
    start = end - (CONTROL_DAYS - 1) * DAY
    control_window = {"start": start.isoformat(), "end": end.isoformat()}
    active = store.active_model()
    if active is None or active["version_id"] != job["active_version_id"]:
        return finish(job, now, status="rejected", control=control_window, decision={
            "accepted": False, "checks": [], "summary": "Рабочая версия сменилась до проверки — задание закрыто."})
    working = store.model_version(active["version_id"])
    if (issue := clean_window_issue(working.metadata["training"]["end"], end)) is not None:
        return finish(job, now, status="rejected", control=control_window,
                      decision={"accepted": False, "checks": [], "summary": issue})

    journal: dict = {}
    history = order_inputs(end + 2 * DAY, data, HISTORY_DAYS, journal)
    first = min(history) + max(LAGS) * DAY
    weather = weather_inputs(first, end, data, journal)
    journal["data_quality"] = quality_summary(history, weather)
    frame = training_frame(history, weather, first)
    # Same rule as validation: at 20:00 before the first control day, the previous day is still open.
    train = frame[frame["date"] < start - DAY]
    control = frame[frame["date"].between(start, end)]
    if len(control) != CONTROL_DAYS * len(ZONES):
        raise ValueError("Для контрольной недели нужны закрытые дни по всем зонам.")

    report, fitted = evaluate_report(start, train, control[control["date"] == start], weather, journal,
                                     half_life=HALF_LIFE_DAYS)
    candidate = make_model_version(report, *fitted, status="candidate")
    comparison = compare(control, {"active": (working.metadata, load_estimator(working)),
                                   "candidate": (candidate.metadata, fitted[0])})
    decision = decide(comparison["scores"])
    job = finish(job, now, version_id=candidate.metadata["version_id"], control=control_window,
                 training={"end": candidate.metadata["training"]["end"], "half_life_days": HALF_LIFE_DAYS},
                 selected=report["selected"], comparison=comparison, decision=decision)
    if not decision["accepted"]:
        job["status"] = "rejected"
        store.save_candidate(job, model=candidate)
        return job

    context = {**active_context(candidate, fitted[0], train, report),
               "promotion": {"candidate_id": job["candidate_id"], "control": control_window,
                             "scores": {name: {key: value for key, value in item.items() if key != "by_zone"}
                                        for name, item in comparison["scores"].items()},
                             "summary": decision["summary"]}}
    job["status"] = "activated"
    record = activation_record(candidate.metadata["version_id"], context, action="activate",
                               previous=active["version_id"], actor=job["trigger"]["requested_by"], now=now,
                               reason=decision["summary"], candidate_id=job["candidate_id"])
    if not store.activate_model(record, expected=active["version_id"], model=candidate, candidate=job):
        job.update(status="rejected", decision={**decision, "accepted": False,
                                                "summary": "Рабочая версия сменилась во время проверки — новая не активирована."})
        store.save_candidate(job, model=candidate)
    return job


def run_candidate(store: Store, data: Path, job: dict, now: datetime) -> dict:
    job = {**job, "status": "training", "started_at_utc": now.astimezone(timezone.utc).isoformat()}
    store.save_candidate(job)
    try:
        return train_candidate(store, data, job, now)
    except Exception as error:
        failed = finish(job, now, status="failed", error=str(error) or type(error).__name__)
        store.save_candidate(failed)
        return failed


def update_models(store: Store, data: Path, now: datetime) -> dict | None:
    """Worker step: queue a candidate when the retrain rule fires, then process the open job."""
    if (trigger := monitoring_trigger(store, now)) is not None:
        request_candidate(store, trigger, now)
    job = store.open_candidate()
    return run_candidate(store, data, job, now) if job is not None else None


def rollback(store: Store, *, expected: str, actor: str, now: datetime) -> dict:
    """Return to the version that was working before the current one. Published forecasts stay as they are."""
    active = store.active_model()
    if active is None or active["version_id"] != expected:
        raise LifecycleConflict("Рабочая версия уже сменилась. Обновите страницу.")
    current = next((record for record in store.activations(limit=1)), None)
    if current is None or current["version_id"] != expected:
        raise LifecycleConflict("Журнал активаций не совпадает с рабочей версией.")
    previous = current["previous_version_id"]
    if not previous:
        raise ValueError("Предыдущей рабочей версии нет.")
    context = store.activation_context(previous)
    if context is None:
        raise ValueError("Для предыдущей версии не сохранён контекст прогноза.")
    record = activation_record(previous, context, action="rollback", previous=expected, actor=actor, now=now,
                               reason="Администратор вернул предыдущую рабочую версию.")
    if not store.activate_model(record, expected=expected):
        raise LifecycleConflict("Рабочая версия уже сменилась. Обновите страницу.")
    return {key: value for key, value in record.items() if key != "context"}
