import argparse
import errno
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from uuid import uuid4

from .modeling.report import FIRST_MODEL_DATE, build_report
from .pipeline.forecast import forecast_cutoff
from .pipeline.observations import collect_closed_days, observation_record
from .pipeline.orders import MOSCOW
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


def publish_daily(target: date, data: Path, force: bool = False) -> None:
    store = result_store(data)
    location = store.location(EVALUATIONS, target)
    with result_lock(store, data):
        if store.has(EVALUATIONS, target) and not force:
            print(f"Прогноз на {target} уже опубликован.", flush=True)
            return

        started = datetime.now(timezone.utc)
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
            report = build_report(target, data, journal)
            published = datetime.now(timezone.utc)
            observation = observation_record(target, report, published)
            journal.update(
                status="success",
                step="done",
                forecast_file=location,
                finished_at_utc=published.isoformat(),
            )
            store.publish(EVALUATIONS, target, report, journal, observation)
        except (Exception, KeyboardInterrupt) as error:
            if journal.pop("forecast_file", None) is not None:
                journal["step"] = "publish"
            journal.update(
                status="failed",
                error=str(error) or type(error).__name__,
                finished_at_utc=datetime.now(timezone.utc).isoformat(),
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


def wait_until(deadline: datetime) -> None:
    while (remaining := (deadline - datetime.now(MOSCOW)).total_seconds()) > 0:
        sleep(min(remaining, 60))


def settle_observations(data: Path, now: datetime) -> None:
    store = result_store(data)
    with result_lock(store, data):
        count = collect_closed_days(store, data, now)
    if count:
        print(f"Сверили прогнозы с закрытыми сутками — {count}.", flush=True)


def watch(data: Path) -> None:
    print("Расписание включено. Прогноз на завтра запускается ежедневно в 20:00 мск.", flush=True)
    published_target = None
    settled_on = None
    while True:
        now = datetime.now(MOSCOW)
        target = latest_due(now)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + DAY
        deadline = min(forecast_cutoff(target + DAY), midnight)
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
        print(f"Следующая проверка в {deadline:%d.%m.%Y %H:%M:%S} мск.", flush=True)
        wait_until(deadline)


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
