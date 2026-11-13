from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import fmean
from urllib.parse import urlencode

from .storage import write_text
from .weather import HOUR_SECONDS, LOCATION, MODEL, daily_slots, download, hourly_rows


ENDPOINT = "https://previous-runs-api.open-meteo.com/v1/forecast"
SUFFIX = "_previous_day2"
LEAD_HOURS = 48
# The latest model run for each past hour stands in for the weather that actually happened.
OBSERVED_DIRECTORY = "observed_latest_run"
DAY = timedelta(days=1)
WEATHER_FEATURES = ("mean_temperature_c", "precipitation_mm", "hours_with_precipitation")


def utc_hours(day: date) -> range:
    start = int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())
    return range(start, start + 24 * HOUR_SECONDS, HOUR_SECONDS)


def weather_history(
    first: date, last: date, directory: Path, *, fetch_missing: bool = True, suffix: str = SUFFIX
) -> tuple[dict[date, dict[str, float | None]], int]:
    # The Moscow day starts at 21:00 UTC on the preceding date.
    first_utc = first - DAY
    rows = {}
    for path in sorted(directory.glob("????-??-??_????-??-??.json")):
        start, end = map(date.fromisoformat, path.stem.split("_"))
        if start <= last and end >= first_utc:
            rows.update(hourly_rows(path.read_text(encoding="utf-8"), suffix))

    ranges: list[tuple[date, date]] = []
    days_to_check = (last - first_utc).days + 1 if fetch_missing else 0
    for offset in range(days_to_check):
        day = first_utc + offset * DAY
        if all(hour in rows for hour in utc_hours(day)):
            continue
        if ranges and ranges[-1][1] + DAY == day:
            ranges[-1] = (ranges[-1][0], day)
        else:
            ranges.append((day, day))

    for start, end in ranges:
        query = urlencode(
            {
                **LOCATION,
                "models": MODEL,
                "hourly": f"temperature_2m{suffix},precipitation{suffix}",
                "timeformat": "unixtime",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
        )
        raw = download(f"{ENDPOINT}?{query}")
        fetched = hourly_rows(raw, suffix)
        if not all(hour in fetched for hour in range(
            utc_hours(start).start, utc_hours(end).stop, HOUR_SECONDS
        )):
            raise ValueError(f"Open-Meteo вернул неполный временной диапазон {start} — {end}")
        write_text(directory / f"{start}_{end}.json", raw)
        rows.update(fetched)

    result = {}
    for offset in range((last - first).days + 1):
        day = first + offset * DAY
        temperature, precipitation = daily_slots(rows, day)
        temperatures = [value for value in temperature if value is not None]
        amounts = [value for value in precipitation if value is not None]
        result[day] = {
            "mean_temperature_c": round(fmean(temperatures), 2) if len(temperatures) == 24 else None,
            "precipitation_mm": round(sum(amounts), 2) if len(amounts) == 24 else None,
            "hours_with_precipitation": sum(value > 0 for value in amounts) if len(amounts) == 24 else None,
        }
    downloaded_days = sum((end - start).days + 1 for start, end in ranges)
    return result, downloaded_days


def observed_weather(first: date, last: date, raw: Path) -> dict[date, dict[str, float | None]]:
    """Daily weather that happened in Moscow; it drives synthetic demand, never the model's features."""
    history, _ = weather_history(first, last, raw / "weather" / OBSERVED_DIRECTORY, suffix="")
    return history
