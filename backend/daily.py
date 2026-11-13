import argparse
import errno
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from uuid import uuid4

from .modeling.inference import forecast_from_saved
from .modeling.demo_cycle import ensure_demo
from .modeling.lifecycle import train_first_version, train_for_data_version, update_models
from .modeling.report import FIRST_MODEL_DATE
from .pipeline.drift import persist_input_drift
from .pipeline.forecast import forecast_cutoff
from .pipeline.observations import collect_closed_days, observation_record
from .pipeline.monitoring import persist_decisions
from .pipeline.orders import MOSCOW, SOURCE as ORDERS_SOURCE
from .pipeline.results import EVALUATIONS, FileResults, PostgresResults, StorageUnavailable, latest_location, result_store


DAY = timedelta(days=1)
RETRY_DELAY = timedelta(minutes=5)
DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data"


def latest_due(now: datetime) -> date:
    today = now.astimezone(MOSCOW).date()
    tomorrow = today + DAY
    return tomorrow if now >= forecast_cutoff(tomorrow) else today


@contextmanager
def daily_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The OS releases the lock when the handle closes, including after a crash.
    with path.open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                from msvcrt import LK_NBLCK, locking

                locking(handle.fileno(), LK_NBLCK, 1)
            else:
                from fcntl import LOCK_EX, LOCK_NB, flock

                flock(handle.fileno(), LOCK_EX | LOCK_NB)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise RuntimeError("В этом каталоге уже выполняется ежедневный прогон.") from error
        yield


def result_lock(store: FileResults | PostgresResults, data: Path):
    return store.daily_lock() if isinstance(store, PostgresResults) else daily_lock(data / ".daily.lock")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def publish_daily(target: date, data: Path, force: bool = False, *,
                  store: FileResults | PostgresResults | None = None, clock: Callable[[], datetime] = utc_now) -> None:
    """`store` and `clock` let the isolated lifecycle demo replay past evenings with the same code."""
    store = store or result_store(data)
    location = store.location(EVALUATIONS, target)
    with result_lock(store, data):
        if store.has(EVALUATIONS, target) and not force:
            print(f"Прогноз на {target} уже опубликован.", flush=True)
            return

        started = clock()
        run_id = f"{started:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}"
        journal = {
            "run_id": run_id,
            "started_at_utc": started.isoformat(),
            "target_date": target.isoformat(),
            "mode": "daily",
            "step": "prepare",
        }
        print(f"Готовим прогноз на {target}…", flush=True)
        try:
            active = store.active_model()
            if active is None:
                # A clean stand trains its first working version once; later evenings only predict.
                journal["step"] = "first_model"
                train_first_version(store, data, target, started, actor="worker")
                active = store.active_model()
            journal["step"] = "load_model"
            model = store.model_version(active["version_id"])
            if model.metadata.get("orders_source") != ORDERS_SOURCE:
                journal["step"] = "data_version"
                train_for_data_version(store, data, target, started, actor="worker")
                active = store.active_model()
                journal["step"] = "load_model"
                model = store.model_version(active["version_id"])
            report = forecast_from_saved(target, data, journal, model, active)
            published = clock()
            report = {**report, "published_at_utc": published.astimezone(timezone.utc).isoformat()}
            observation = observation_record(target, report, published)
            journal.update(
                status="success",
                step="done",
                forecast_file=location,
                model_version_id=report["model_version_id"],
                finished_at_utc=published.isoformat(),
            )
            store.publish(EVALUATIONS, target, report, journal, observation)
        except (Exception, KeyboardInterrupt) as error:
            journal.pop("model_version_id", None)
            if journal.pop("forecast_file", None) is not None:
                journal["step"] = "publish"
            journal.update(
                status="failed",
                error=str(error) or type(error).__name__,
                finished_at_utc=clock().isoformat(),
            )
            try:
                previous = latest_location(store)
                if previous is not None:
                    journal["last_forecast_file"] = previous
                store.save_run(journal)
            except (OSError, StorageUnavailable) as log_error:
                print(f"Не удалось записать журнал: {log_error}", file=sys.stderr)
            raise

    print(f"Прогноз на {target} опубликован в {location}.", flush=True)


def wait_until(deadline: datetime, wake: Callable[[], bool] | None = None) -> None:
    while (remaining := (deadline - datetime.now(MOSCOW)).total_seconds()) > 0:
        sleep(min(remaining, 60))
        if wake is not None and wake():
            return


def beat(data: Path, now: datetime, state: str, next_check: datetime | None = None) -> None:
    """Heartbeat for the dashboard and the container healthcheck; never stops the schedule."""
    payload = {"at_utc": now.astimezone(timezone.utc).isoformat(), "state": state,
               "next_check_utc": next_check.astimezone(timezone.utc).isoformat() if next_check else None}
    try:
        result_store(data).beat(payload)
    except (OSError, StorageUnavailable):
        pass


def waiting(data: Path, deadline: datetime) -> bool:
    beat(data, datetime.now(timezone.utc), "waiting", deadline)
    return candidate_waiting(data)


def candidate_waiting(data: Path) -> bool:
    """An administrator may queue a candidate between scheduled checks."""
    try:
        job = result_store(data).open_candidate()
    except (OSError, StorageUnavailable):
        return False
    return job is not None and job["status"] == "queued"


def prepare_data_version(data: Path, now: datetime) -> None:
    """Switch the working model to the current data version before drift and monitoring read new files."""
    store = result_store(data)
    with result_lock(store, data):
        version = train_for_data_version(store, data, latest_due(now), now, actor="worker")
    if version is not None:
        print(f"Рабочая модель переобучена на новой версии данных {ORDERS_SOURCE}: {version}.", flush=True)


def maintain_models(data: Path, now: datetime) -> None:
    store = result_store(data)
    with result_lock(store, data):
        job = update_models(store, data, now)
    if job is not None:
        outcome = job.get("decision", {}).get("summary") or job.get("error") or ""
        print(f"Проверка новой версии модели завершена: {job['status']}. {outcome}", flush=True)


def settle_observations(data: Path, now: datetime) -> None:
    store = result_store(data)
    with result_lock(store, data):
        count = collect_closed_days(store, data, now)
        decisions = persist_decisions(store, now)
    if count:
        print(f"Сверили прогнозы с закрытыми сутками — {count}.", flush=True)
    if decisions:
        print(f"Сохранили решения мониторинга по зонам — {decisions}.", flush=True)


def check_input_drift(data: Path, now: datetime) -> None:
    store = result_store(data)
    with result_lock(store, data):
        report = persist_input_drift(store, data, now)
    if report:
        print(f"Сохранили проверку входных данных по {report['end_date']} включительно.", flush=True)


def watch(data: Path) -> None:
    print("Расписание включено. Прогноз на завтра запускается ежедневно в 20:00 мск.", flush=True)
    published_target = None
    settled_on = None
    drift_checked_on = None
    demo_checked_on = None
    while True:
        now = datetime.now(MOSCOW)
        beat(data, now, "working")
        target = latest_due(now)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + DAY
        deadline = min(forecast_cutoff(target + DAY), midnight)
        try:
            prepare_data_version(data, now)
        except Exception as error:
            deadline = min(deadline, datetime.now(MOSCOW) + RETRY_DELAY)
            print(f"Переход на новую версию данных прерван. {error}", file=sys.stderr, flush=True)
        if published_target != target:
            try:
                publish_daily(target, data)
            except Exception as error:
                deadline = min(deadline, datetime.now(MOSCOW) + RETRY_DELAY)
                print(f"Прогон прерван. {error}", file=sys.stderr, flush=True)
            else:
                published_target = target
        if settled_on != now.date():
            try:
                settle_observations(data, now)
            except Exception as error:
                deadline = min(deadline, datetime.now(MOSCOW) + RETRY_DELAY)
                print(f"Сверка фактов прервана. {error}", file=sys.stderr, flush=True)
            else:
                settled_on = now.date()
        if drift_checked_on != now.date():
            try:
                check_input_drift(data, now)
            except Exception as error:
                deadline = min(deadline, datetime.now(MOSCOW) + RETRY_DELAY)
                print(f"Проверка входных данных прервана. {error}", file=sys.stderr, flush=True)
            else:
                drift_checked_on = now.date()
        try:
            maintain_models(data, now)
        except Exception as error:
            deadline = min(deadline, datetime.now(MOSCOW) + RETRY_DELAY)
            print(f"Обновление модели прервано. {error}", file=sys.stderr, flush=True)
        if demo_checked_on != now.date():
            # Built once per demo version on isolated data; a failure is retried the next day.
            demo_checked_on = now.date()
            try:
                if ensure_demo(data) is not None:
                    print("Подготовили учебный цикл обновления модели на изолированных данных.", flush=True)
            except Exception as error:
                print(f"Учебный цикл не подготовлен. {error}", file=sys.stderr, flush=True)
        print(f"Следующая проверка в {deadline:%d.%m.%Y %H:%M:%S} мск.", flush=True)
        beat(data, datetime.now(MOSCOW), "waiting", deadline)
        wait_until(deadline, lambda: waiting(data, deadline))


def main() -> int:
    parser = argparse.ArgumentParser(description="Ежедневное обновление прогноза доставки")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="ожидать вечернего запуска каждый день")
    mode.add_argument("--date", type=date.fromisoformat, help="запустить расчёт на доступную дату")
    parser.add_argument("--force", action="store_true", help="заменить сохранённый прогноз после успешного расчёта")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.getenv("PULT_DATA_DIR", str(DEFAULT_DATA))),
        help="каталог входных данных и результатов",
    )
    args = parser.parse_args()

    due = latest_due(datetime.now(MOSCOW))
    target = args.date or due
    if args.watch and args.force:
        parser.error("--force доступен для разового запуска")
    if not FIRST_MODEL_DATE <= target <= due:
        parser.error(f"дата прогноза должна быть в диапазоне {FIRST_MODEL_DATE} — {due}")

    try:
        if args.watch:
            watch(args.data_dir)
        else:
            publish_daily(target, args.data_dir, args.force)
    except KeyboardInterrupt:
        print("\nПланировщик остановлен.", flush=True)
        return 130
    except Exception as error:
        print(f"Прогон прерван. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
