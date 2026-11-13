"""Compare model inputs on closed dates with a frozen reference window."""
import json
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from statistics import median

from backend.modeling.features import ORDER_FEATURES, ORDER_INPUT_LAGS, features_for_day

from .orders import MOSCOW, SOURCE, SOURCE_MARKER, ZONES, count_orders
from .results import FileResults, PostgresResults
from .weather_archive import WEATHER_FEATURES, weather_history


# v2: the reference is fixed again on the weather-aware data version; v1 reports stay as they were.
RULE_VERSION = "input-drift-v2"
DAY = timedelta(days=1)
REFERENCE_DAYS = 56
CURRENT_DAYS = 14
REFERENCE_MIN = 42
CURRENT_MIN = 10
THRESHOLD = 0.45
SERIES = tuple((zone.id, feature) for zone in ZONES for feature in ORDER_FEATURES) + tuple(
    (None, feature) for feature in WEATHER_FEATURES
)


def dates_between(start: date, end: date):
    for offset in range((end - start).days + 1):
        yield start + offset * DAY


def load_days(data: Path, start: date, end: date) -> list[dict]:
    history = {}
    order_ids: set[str] = set()
    for day in dates_between(start - max(ORDER_INPUT_LAGS) * DAY, end - 2 * DAY):
        path = data / "raw" / "orders" / f"{day}.csv"
        if path.is_file():
            history[day] = count_orders(day, path, order_ids)
    weather, _ = weather_history(start, end, data / "raw" / "weather" / "previous_runs_48h", fetch_missing=False)

    samples = []
    for day in dates_between(start, end):
        orders = {}
        if all(day - lag * DAY in history for lag in ORDER_INPUT_LAGS):
            orders = {row["zone_id"]: {name: row[name] for name in ORDER_FEATURES}
                      for row in features_for_day(day, history, {})}
        samples.append({"date": day.isoformat(), "orders": orders, "weather": weather[day]})
    return samples


def series_values(samples: list[dict], zone: str | None, feature: str) -> list[tuple[int, float]]:
    values = []
    for row in samples:
        inputs = row["weather"] if zone is None else row["orders"].get(zone, {})
        value = inputs.get(feature)
        if value is not None:
            values.append((date.fromisoformat(row["date"]).weekday(), value))
    return values


def enough(values: list[tuple[int, float]], zone: str | None, *, reference: bool) -> bool:
    minimum = REFERENCE_MIN if reference else CURRENT_MIN
    if len(values) < minimum:
        return False
    if zone is None:
        return True
    weekdays = [weekday for weekday, _ in values]
    return all(weekdays.count(weekday) >= (4 if reference else 1) for weekday in range(7))


def distribution_distance(reference: list[float], current: list[float]) -> float:
    """Largest gap between empirical CDFs, including tied values."""
    reference, current = sorted(reference), sorted(current)
    numerator = max(abs(bisect_right(reference, value) * len(current)
                        - bisect_right(current, value) * len(reference))
                    for value in set(reference + current))
    return numerator / (len(reference) * len(current))


def compare_series(feature: str, zone: str | None, reference: list[tuple[int, float]],
                   current: list[tuple[int, float]], fixed: bool) -> dict:
    reference_values = [value for _, value in reference]
    current_values = [value for _, value in current]
    distance = None
    status = "insufficient"
    if fixed and enough(current, zone, reference=False):
        if zone is not None:
            typical = {day: median(value for weekday, value in reference if weekday == day) for day in range(7)}
            reference_values_adjusted = [value - typical[weekday] for weekday, value in reference]
            current_values_adjusted = [value - typical[weekday] for weekday, value in current]
        else:
            reference_values_adjusted, current_values_adjusted = reference_values, current_values
        distance = distribution_distance(reference_values_adjusted, current_values_adjusted)
        status = "shift" if distance >= THRESHOLD else "steady"
    return {"feature": feature, "status": status, "distance": distance,
            "reference_count": len(reference_values), "current_count": len(current_values),
            "reference_median": median(reference_values) if reference_values else None,
            "current_median": median(current_values) if current_values else None}


def window(samples: list[dict]) -> dict:
    return {"start_date": samples[0]["date"], "end_date": samples[-1]["date"], "days": len(samples),
            "sha256": sha256(json.dumps(samples, sort_keys=True, allow_nan=False).encode()).hexdigest()}


def persist_input_drift(store: FileResults | PostgresResults, data: Path, now: datetime) -> dict | None:
    """Run under the daily lock. Persist one immutable report per closed date."""
    orders = data / "raw" / "orders"
    marker = orders / SOURCE_MARKER
    current = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
    if current != SOURCE and any(orders.glob("????-??-??.csv")):
        return None  # files of an older data version: never mix versions in one reference
    end = now.astimezone(MOSCOW).date() - DAY
    start = end - (CURRENT_DAYS - 1) * DAY
    latest = store.latest_drift(RULE_VERSION)
    if latest and latest["end_date"] >= end.isoformat():
        return None

    reference = store.drift_reference(RULE_VERSION)
    if reference and reference["window"]["end_date"] >= start.isoformat():
        return None

    first = start if reference else start - REFERENCE_DAYS * DAY
    samples = load_days(data, first, end)
    current = samples[-CURRENT_DAYS:]
    reference_days = reference["samples"] if reference else samples[:-CURRENT_DAYS]
    reference_window = reference["window"] if reference else window(reference_days)
    reference_values = {(zone, feature): series_values(reference_days, zone, feature) for zone, feature in SERIES}
    fixed = all(enough(values, zone, reference=True) for (zone, _), values in reference_values.items())

    orders: dict[str, list[dict]] = {zone.id: [] for zone in ZONES}
    weather: list[dict] = []
    for zone, feature in SERIES:
        row = compare_series(feature, zone, reference_values[zone, feature], series_values(current, zone, feature), fixed)
        (weather if zone is None else orders[zone]).append(row)

    report = {"rule_version": RULE_VERSION, "end_date": end.isoformat(),
              "saved_at_utc": now.astimezone(timezone.utc).isoformat(),
              "reference": reference_window, "reference_fixed": fixed, "current": window(current),
              "rule": {"distance_threshold": THRESHOLD, "reference_min": REFERENCE_MIN, "current_min": CURRENT_MIN},
              "orders": orders, "weather": weather}
    new_reference = {"window": reference_window, "samples": reference_days} if fixed and reference is None else None
    store.save_drift(report, new_reference)
    return report
