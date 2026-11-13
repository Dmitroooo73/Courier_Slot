import csv
import io
import os
import re
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
# Data versions. v2 demand reacts to the weather that actually happened; v1 is kept for archived forecasts.
SOURCE = "synthetic-weather-v2"
LEGACY_SOURCE = "synthetic"
SOURCE_MARKER = "SOURCE"
RAIN_EFFECT = 0.015      # +1.5% orders per hour with precipitation above a typical day
RAIN_HOURS_CAP = 12
COLD_EFFECT = 0.006      # +0.6% orders per degree of cold above a typical day
COLD_FROM_C = 8.0
COLD_SPAN_C = 25.0
# Moscow, Nov 2025 - Nov 2026: 4.05 h with precipitation and a cold term of 4.89 on an average day.
# Centring on them keeps the yearly mean demand of each zone, so capacities stay meaningful.
TYPICAL_RAIN_HOURS = 4.0
TYPICAL_COLD = 5.0


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


def weather_factor(weather: dict | None) -> float:
    """Synthetic demand multiplier: rain and cold keep people at home and raise delivery orders."""
    weather = weather or {}
    factor = 1.0
    if (hours := weather.get("hours_with_precipitation")) is not None:
        factor += RAIN_EFFECT * (min(hours, RAIN_HOURS_CAP) - TYPICAL_RAIN_HOURS)
    if (temperature := weather.get("mean_temperature_c")) is not None:
        factor += COLD_EFFECT * (min(max(COLD_FROM_C - temperature, 0.0), COLD_SPAN_C) - TYPICAL_COLD)
    return factor


def synthetic_records(day: date, weather: dict | None = None, *, source: str = SOURCE):
    midnight = datetime.combine(day, time.min, tzinfo=MOSCOW)
    factor = weather_factor(weather) if source == SOURCE else 1.0
    for zone in ZONES:
        # The same noise draw in both versions: v2 differs from v1 only by the weather factor.
        rng = Random(f"{SEED}:{day}:{zone.id}")
        demand = zone.average_orders * WEEKDAY_FACTORS[day.weekday()] * factor
        count = max(1, round(rng.normalvariate(demand, demand * 0.06)))
        minutes = sorted(rng.randrange(24 * 60) for _ in range(count))
        for index, minute in enumerate(minutes, start=1):
            created_at = midnight + timedelta(minutes=minute)
            yield (f"{day}-{zone.id}-{index}", created_at.isoformat(), zone.id)


def generate_event_log(day: date, path: Path, weather: dict | None = None, *, source: str = SOURCE) -> None:
    """Synthetic service log: free text, optional fields and mixed event types."""
    lines = [f"{day}T00:00:00+03:00 INFO service started"]
    if source == SOURCE:
        # Recorded for audit; the parser treats it as a service line, not an order.
        values = weather or {}
        lines.append(f"{day}T00:00:00+03:00 INFO weather.observed "
                     f"mean_temperature_c={values.get('mean_temperature_c')} "
                     f"hours_with_precipitation={values.get('hours_with_precipitation')} "
                     f"demand_factor={weather_factor(weather):.4f}")
    for index, (order_id, created_at, zone) in enumerate(synthetic_records(day, weather, source=source)):
        fields = f"order_id={order_id} zone={zone}" if index % 2 else f"zone={zone} order_id={order_id}"
        lines.append(f"{created_at} INFO order.created {fields} channel=demo | Order accepted")
        if index % 29 == 0:
            # A retry is a second delivery of an event, not another order.
            lines.append(lines[-1] + " (redelivered)")
        if index % 53 == 0:
            lines.append(f"{created_at} DEBUG service heartbeat")
    lines.append(f"{day}T23:59:59+03:00 INFO export.complete")
    write_text(path, "\n".join(lines) + "\n")


def import_event_log(day: date, source: Path, destination: Path) -> dict[str, int]:
    """Normalize a completed log atomically; reject ambiguous order events."""
    records: dict[str, tuple[str, str, str]] = {}
    counts = dict.fromkeys((zone.id for zone in ZONES), 0)
    stats = {"events": 0, "duplicate_events": 0, "ignored_lines": 0}
    complete = False
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split(maxsplit=3)
        kind = parts[2] if len(parts) >= 3 else ""
        if kind == "export.complete":
            stamp = datetime.fromisoformat(parts[0])
            if stamp.tzinfo is None or stamp.astimezone(MOSCOW).date() != day:
                raise ValueError(f"{source}:{line_number}: неверная дата завершения выгрузки")
            complete = True
            continue
        if kind != "order.created":
            stats["ignored_lines"] += 1
            continue
        if complete:
            raise ValueError(f"{source}:{line_number}: заказ после завершения выгрузки")
        try:
            fields = re.findall(r"\b(order_id|zone)=([^\s|]+)", parts[3])
            if len(fields) != 2 or len(dict(fields)) != 2:
                raise ValueError("неоднозначные поля заказа")
            values = dict(fields)
            order_id, zone = values["order_id"], values["zone"]
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", order_id) or zone not in counts:
                raise ValueError("неверный ID или зона")
            stamp = datetime.fromisoformat(parts[0])
            if stamp.tzinfo is None or stamp.astimezone(MOSCOW).date() != day:
                raise ValueError("заказ за пределами суток")
        except (ValueError, KeyError, IndexError) as error:
            raise ValueError(f"{source}:{line_number}: некорректное событие заказа ({error})") from error
        record = (order_id, stamp.astimezone(MOSCOW).isoformat(), zone)
        if order_id in records:
            if records[order_id] != record:
                raise ValueError(f"{source}:{line_number}: конфликт событий одного заказа")
            stats["duplicate_events"] += 1
        else:
            records[order_id] = record
            counts[zone] += 1
            stats["events"] += 1
    if not complete or 0 in counts.values():
        raise ValueError(f"{source}: неполная выгрузка суток или отсутствуют зоны")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(ORDER_FIELDS)
    writer.writerows(records.values())
    write_text(destination, buffer.getvalue())
    return stats


def orders_directory(raw: Path, source: str = SOURCE) -> Path:
    """Current data version in raw/orders; older versions under raw/archive/<source>/orders."""
    return raw / "orders" if source == SOURCE else raw / "archive" / source / "orders"


def ensure_layout(directory: Path) -> None:
    """Move CSVs and event logs of an older data version to the archive unchanged, once."""
    marker = directory / SOURCE_MARKER
    if marker.is_file():
        current = marker.read_text(encoding="utf-8").strip()
        if current == SOURCE:
            return
        archived = current
    else:
        archived = LEGACY_SOURCE
    raw = directory.parent
    if directory.is_dir() and any(directory.iterdir()):
        target = raw / "archive" / archived
        if (target / "orders").exists():
            raise ValueError(f"Архив версии данных {archived} уже существует: {target}")
        target.mkdir(parents=True, exist_ok=True)
        os.replace(directory, target / "orders")
        if (raw / "order_events").is_dir():
            os.replace(raw / "order_events", target / "order_events")
    directory.mkdir(parents=True, exist_ok=True)
    write_text(marker, SOURCE + "\n")


def generate_orders(day: date, path: Path, weather: dict | None = None, *, source: str = SOURCE) -> None:
    events = path.parent.parent / "order_events" / f"{day}.log"
    if not events.is_file():
        if source == SOURCE and weather is None:
            from .weather_archive import observed_weather

            weather = observed_weather(day, day, path.parent.parent)[day]
        generate_event_log(day, events, weather, source=source)
    import_event_log(day, events, path)


def load_history(
    target: date, directory: Path, days: int, *, source: str = SOURCE
) -> tuple[dict[date, dict[str, int]], int]:
    if source == SOURCE:
        ensure_layout(directory)
    # The last complete day at 20:00 on D-1 is D-2.
    needed = [target - timedelta(days=lag) for lag in range(days + 1, 1, -1)]
    missing = [day for day in needed if not (directory / f"{day}.csv").exists()]
    weather: dict = {}
    if missing and source == SOURCE:
        from .weather_archive import observed_weather

        # One request for the whole range instead of one per day.
        weather = observed_weather(min(missing), max(missing), directory.parent)
    history = {}
    order_ids: set[str] = set()
    for day in needed:
        path = directory / f"{day}.csv"
        if not path.exists():
            generate_orders(day, path, weather.get(day), source=source)
        history[day] = count_orders(day, path, order_ids)
    return history, len(missing)


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
