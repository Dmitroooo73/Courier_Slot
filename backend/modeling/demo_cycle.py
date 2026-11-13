"""Replay the whole model lifecycle on isolated, explicitly shifted training data.

The replay runs the worker code with a simulated clock in a temporary catalogue:
normal week -> manual check (rejected) -> demand shift -> warning -> alert ->
candidate (activated) -> forecasts by the new version -> rollback.
Only the summary is saved to the working storage, under its own key.
"""
import argparse
import csv
import io
import os
import shutil
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.pipeline.monitoring import RULE_VERSION as MONITORING_RULE, persist_decisions
from backend.pipeline.observations import collect_closed_days
from backend.pipeline.orders import MOSCOW, SOURCE, SOURCE_MARKER, ZONES, load_history
from backend.pipeline.results import EVALUATIONS, FileResults, result_store

from .lifecycle import RULES, request_candidate, rollback, train_first_version, update_models


DEMO_VERSION = "lifecycle-demo-v3"  # v3: weather-aware orders and the promotion-v2 rule
FIRST_TARGET = date(2026, 10, 13)
MANUAL_CHECK = date(2026, 10, 19)
SHIFT_START = date(2026, 10, 20)
LAST_DAY = date(2026, 11, 12)
SHIFT_ZONE = "east"
SHIFT_FACTOR = 1.35
DAY = timedelta(days=1)


def shift_zone(directory: Path, days: list[date], zone: str, factor: float) -> None:
    """Add clearly marked synthetic orders to closed-day CSVs of the isolated catalogue."""
    for day in days:
        path = directory / f"{day}.csv"
        rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
        extra = round(sum(row[2] == zone for row in rows[1:]) * (factor - 1))
        rows += [[f"{day}-{zone}-shift-{index}", f"{day}T12:{index % 60:02d}:00+03:00", zone] for index in range(extra)]
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\n").writerows(rows)
        path.write_text(buffer.getvalue(), encoding="utf-8", newline="")


def moscow(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=MOSCOW)


def prepare_world(source: Path, world: Path) -> None:
    orders = world / "raw" / "orders"
    orders.mkdir(parents=True)
    (orders / SOURCE_MARKER).write_text(SOURCE + "\n", encoding="utf-8")
    marker = source / "raw" / "orders" / SOURCE_MARKER
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == SOURCE:
        for path in (source / "raw" / "orders").glob("????-??-??.csv"):
            shutil.copy2(path, orders / path.name)
        if (source / "raw" / "order_events").is_dir():
            shutil.copytree(source / "raw" / "order_events", world / "raw" / "order_events")
    if (source / "raw" / "weather").is_dir():
        shutil.copytree(source / "raw" / "weather", world / "raw" / "weather")
    # Make sure every closed day of the replay exists before the shift is applied.
    load_history(LAST_DAY + 2 * DAY, orders, (LAST_DAY - FIRST_TARGET).days + 380)
    shift_zone(orders, [SHIFT_START + offset * DAY for offset in range((LAST_DAY - SHIFT_START).days + 1)],
               SHIFT_ZONE, SHIFT_FACTOR)


def brief(job: dict) -> dict:
    keys = ("candidate_id", "trigger", "status", "control", "training", "selected", "decision", "error", "version_id")
    result = {key: job[key] for key in keys if key in job}
    if "comparison" in job:
        result["scores"] = {name: {key: value for key, value in score.items() if key != "by_zone"}
                            for name, score in job["comparison"]["scores"].items()}
        result["zone_scores"] = {name: score["by_zone"][SHIFT_ZONE] for name, score in job["comparison"]["scores"].items()}
    return result


def replay(source: Path, world: Path) -> dict:
    from backend.daily import publish_daily  # the worker imports this module

    prepare_world(source, world)
    store = FileResults(world)
    events = []
    first = train_first_version(store, world, FIRST_TARGET, moscow(FIRST_TARGET - DAY, 19, 0), actor="demo")
    events.append({"date": FIRST_TARGET.isoformat(), "kind": "initialize", "version_id": first})
    seen_status = "pending"
    alert_trigger = None

    def publish(target: date) -> None:
        evening = moscow(target - DAY, 20, 5)
        publish_daily(target, world, store=store, clock=lambda: evening.astimezone(timezone.utc))

    publish(FIRST_TARGET)
    day = FIRST_TARGET
    while day <= LAST_DAY:
        # Calendar order: tomorrow's forecast at 20:00 today, then today's close after midnight.
        if day < LAST_DAY:
            publish(day + DAY)
        morning = moscow(day + DAY, 0, 10)
        collect_closed_days(store, world, morning)
        persist_decisions(store, morning)
        status = next((record["status"] for record in store.decisions(MONITORING_RULE, SHIFT_ZONE, limit=1)), "pending")
        if status != seen_status and seen_status != "pending":
            events.append({"date": day.isoformat(), "kind": "monitoring", "status": status,
                           "previous": seen_status, "zone_id": SHIFT_ZONE})
        seen_status = status
        if day == MANUAL_CHECK:
            request_candidate(store, {"kind": "manual", "end_date": day.isoformat(), "zones": [],
                                      "requested_by": "admin:demo"}, morning)
        if (job := update_models(store, world, morning)) is not None:
            events.append({"date": day.isoformat(), "kind": "candidate", "job": brief(job)})
            if job["trigger"]["kind"] == "monitoring":
                alert_trigger = job["trigger"]
        day += DAY

    reports = {target.isoformat(): store.read(EVALUATIONS, target)["model_version_id"]
               for target in store.dates() if FIRST_TARGET <= target <= LAST_DAY}
    active = store.active_model()["version_id"]
    checks = {}
    if alert_trigger is not None:
        _, created = request_candidate(store, alert_trigger, moscow(LAST_DAY + DAY, 0, 20))
        checks["repeated_signal_creates_job"] = created
    if active != first:
        record = rollback(store, expected=active, actor="admin:demo", now=moscow(LAST_DAY + DAY, 0, 30))
        events.append({"date": (LAST_DAY + DAY).isoformat(), "kind": "rollback", "version_id": record["version_id"],
                       "previous_version_id": record["previous_version_id"]})
        after = {target.isoformat(): store.read(EVALUATIONS, target)["model_version_id"]
                 for target in store.dates() if FIRST_TARGET <= target <= LAST_DAY}
        checks["published_forecasts_unchanged_after_rollback"] = after == reports
        checks["working_version_after_rollback"] = store.active_model()["version_id"]

    observations = sorted(store.observations(limit=None), key=lambda record: record["target_date"])
    timeline = [{"date": record["target_date"], "version_id": record["model_version_id"],
                 "forecast": record["forecasts"], "actual": record["actuals"]}
                for record in observations if record["actuals"] is not None]
    versions = {}
    for version_id in {first, active, *reports.values()}:
        metadata = store.model_metadata(version_id)
        versions[version_id] = {key: metadata[key] for key in ("model", "uses_weather", "status", "training", "parameters")}
    decisions = list(reversed(store.decisions(MONITORING_RULE, SHIFT_ZONE, limit=60)))
    return {
        "demo_version": DEMO_VERSION,
        "rules": RULES,
        "monitoring_rule": MONITORING_RULE,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "shift": {"zone_id": SHIFT_ZONE, "factor": SHIFT_FACTOR, "start": SHIFT_START.isoformat()},
        "period": {"start": FIRST_TARGET.isoformat(), "end": LAST_DAY.isoformat()},
        "first_version_id": first,
        "events": events,
        "timeline": timeline,
        "decisions": [{key: record[key] for key in ("end_date", "status", "weeks")} for record in decisions],
        "versions": versions,
        "checks": checks,
        "candidates": len(store.candidates(limit=None)),
    }


def build_demo(source: Path) -> dict:
    with TemporaryDirectory(prefix="pult-lifecycle-demo-") as temporary, redirect_stdout(io.StringIO()):
        return replay(source, Path(temporary))


def ensure_demo(data: Path) -> dict | None:
    """Worker step: build the replay once per demo version so a clean stand can show it."""
    store = result_store(data)
    if store.lifecycle_demo(DEMO_VERSION) is not None:
        return None
    summary = build_demo(data)
    store.save_lifecycle_demo(summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Проиграть учебный цикл обновления модели на изолированных данных")
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("PULT_DATA_DIR", "data")),
                        help="рабочий каталог с исходными заказами и погодой (не изменяется)")
    parser.add_argument("--force", action="store_true", help="пересчитать и заменить сохранённый итог демонстрации")
    args = parser.parse_args()
    store = result_store(args.data_dir)
    if store.lifecycle_demo(DEMO_VERSION) is not None and not args.force:
        print("Учебный цикл уже рассчитан. Для пересчёта добавьте --force.")
        return 0
    try:
        summary = build_demo(args.data_dir)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Учебный цикл прерван. {error}", file=sys.stderr)
        return 1
    store.save_lifecycle_demo(summary)
    for event in summary["events"]:
        if event["kind"] == "candidate":
            job = event["job"]
            print(f"{event['date']}: {job.get('decision', {}).get('summary') or job.get('error') or job['status']}")
        else:
            print(f"{event['date']}: {event['kind']} {event.get('status', '')}")
    print("Проверки:", summary["checks"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
