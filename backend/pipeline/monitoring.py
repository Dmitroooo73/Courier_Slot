"""Immutable daily error decisions, rebuilt from original forecast observations."""
import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from statistics import mean

from .orders import MOSCOW, ZONES
from .results import FileResults, PostgresResults


RULE_VERSION = "weekly-error-v1"
WEEK_DAYS = 7
WINDOW_DAYS = 14
LIMIT_FACTOR = 1.3
ACTIONS = {"pending": "collect", "normal": "observe", "warning": "check_next_week", "alert": "retrain_recommended"}


def closed_point(record: dict, zone: str, today: date) -> dict | None:
    target = date.fromisoformat(record["target_date"])
    if target >= today or record["actuals"] is None:
        return None
    values = [record[key].get(zone) for key in ("actuals", "forecasts", "validation_mae")]
    if any(not isinstance(value, (int, float)) or isinstance(value, bool)
           or not math.isfinite(value) or value < 0 for value in values):
        return None
    actual, forecast, reference = values
    return {"date": target.isoformat(), "actual": actual, "forecast": forecast, "reference": reference}


def assess(points: list[dict]) -> tuple[list[dict], str]:
    weeks = []
    for block in (points[:-WEEK_DAYS], points[-WEEK_DAYS:]):
        if not block:
            continue
        error = mean(abs(point["actual"] - point["forecast"]) for point in block)
        limit = mean(point["reference"] for point in block) * LIMIT_FACTOR
        complete = len(block) == WEEK_DAYS
        weeks.append({"start": block[0]["date"], "end": block[-1]["date"], "days": len(block),
                      "error": error, "limit": limit, "complete": complete,
                      "aboveLimit": complete and error > limit})
    exceeded = sum(week["aboveLimit"] for week in weeks)
    status = "alert" if exceeded == 2 else "warning" if exceeded else (
        "normal" if any(week["complete"] for week in weeks) else "pending")
    return weeks, status


def decision_records(observations: list[dict], now: datetime):
    """Each record uses only days up to its own end date, including on recovery."""
    today = now.astimezone(MOSCOW).date()
    records = sorted(observations, key=lambda record: record["target_date"])
    for zone in ZONES:
        points = []
        evidence = []
        for record in records:
            point = closed_point(record, zone.id, today)
            if point is None:
                continue
            if points and date.fromisoformat(point["date"]) - date.fromisoformat(points[-1]["date"]) != timedelta(days=1):
                points, evidence = [], []
            points = (points + [point])[-WINDOW_DAYS:]
            evidence = (evidence + [{**point, "model": record["model"], "uses_weather": record["uses_weather"],
                                    "published_at_utc": record["published_at_utc"],
                                    "observed_at_utc": record["observed_at_utc"],
                                    "orders_source": record["orders_source"]}])[-WINDOW_DAYS:]
            weeks, status = assess(points)
            fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True, allow_nan=False).encode()).hexdigest()
            yield {"zone_id": zone.id, "end_date": point["date"], "start_date": points[0]["date"],
                   "rule_version": RULE_VERSION, "saved_at_utc": now.astimezone(timezone.utc).isoformat(),
                   "status": status, "action": ACTIONS[status], "points": points, "weeks": weeks,
                   "rule": {"week_days": WEEK_DAYS, "window_days": WINDOW_DAYS, "limit_factor": LIMIT_FACTOR},
                   "source_sha256": fingerprint,
                   "orders_sources": sorted({item["orders_source"] for item in evidence})}


def persist_decisions(store: FileResults | PostgresResults, now: datetime) -> int:
    # Read all original observations so recovery can fill missed decisions after downtime.
    # The worker holds the daily lock; PostgreSQL also enforces the unique decision key.
    return store.save_decisions(list(decision_records(store.observations(limit=None), now)))
