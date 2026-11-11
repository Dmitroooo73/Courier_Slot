import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from random import Random

from .storage import write_text


MOSCOW = timezone(timedelta(hours=3), "MSK")
HISTORY_DAYS = 14
SEED = 42
ORDER_FIELDS = ("order_id", "created_at", "zone_id")
WEEKDAY_FACTORS = (0.93, 0.95, 0.97, 1.0, 1.13, 1.19, 1.09)


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    average_orders: int
    capacity: int


ZONES = (
    Zone("center", "Центр", 155, 180),
    Zone("north", "Север", 116, 120),
    Zone("east", "Восток", 110, 135),
    Zone("south", "Юг", 95, 125),
    Zone("west", "Запад", 105, 105),
    Zone("northwest", "Северо-запад", 81, 100),
)


def generate_orders(day: date, path: Path) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(ORDER_FIELDS)
    midnight = datetime.combine(day, time.min, tzinfo=MOSCOW)
    for zone in ZONES:
        rng = Random(f"{SEED}:{day}:{zone.id}")
        demand = zone.average_orders * WEEKDAY_FACTORS[day.weekday()]
        count = max(1, round(rng.normalvariate(demand, demand * 0.06)))
        minutes = sorted(rng.randrange(24 * 60) for _ in range(count))
        for index, minute in enumerate(minutes, start=1):
            created_at = midnight + timedelta(minutes=minute)
            writer.writerow((f"{day}-{zone.id}-{index}", created_at.isoformat(), zone.id))
    write_text(path, buffer.getvalue())


def load_history(
    target: date, directory: Path, days: int
) -> tuple[dict[date, dict[str, int]], int]:
    history = {}
    order_ids: set[str] = set()
    new_days = 0
    # The last complete day at 20:00 on D-1 is D-2.
    for lag in range(days + 1, 1, -1):
        day = target - timedelta(days=lag)
        path = directory / f"{day}.csv"
        if not path.exists():
            generate_orders(day, path)
            new_days += 1
        history[day] = count_orders(day, path, order_ids)
    return history, new_days


def count_orders(day: date, path: Path, order_ids: set[str]) -> dict[str, int]:
    counts = dict.fromkeys((zone.id for zone in ZONES), 0)
    with path.open(encoding="utf-8", newline="") as source:
        rows = csv.DictReader(source, strict=True)
        if rows.fieldnames != list(ORDER_FIELDS):
            raise ValueError(f"{path}: неверные столбцы CSV")

        for row in rows:
            order_id = row["order_id"]
            zone_id = row["zone_id"]
            if None in row or not order_id or order_id in order_ids or zone_id not in counts:
                raise ValueError(f"{path}:{rows.line_num}: неверная зона, ID или дубликат")
            try:
                created_at = datetime.fromisoformat(row["created_at"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"{path}:{rows.line_num}: неверное время заказа") from error
            if created_at.tzinfo is None or created_at.astimezone(MOSCOW).date() != day:
                raise ValueError(f"{path}:{rows.line_num}: заказ за пределами суток")
            counts[zone_id] += 1
            order_ids.add(order_id)
    if 0 in counts.values():
        raise ValueError(f"{path}: отсутствуют заказы по одной или нескольким зонам")
    return counts


def write_daily_orders(path: Path, history: dict[date, dict[str, int]]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("date", "zone_id", "order_count"))
    for day, counts in history.items():
        for zone_id, count in counts.items():
            writer.writerow((day, zone_id, count))
    write_text(path, buffer.getvalue())
