import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import IncompleteRead
from pathlib import Path
from statistics import fmean
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .orders import MOSCOW
from .storage import write_text


DEMO_DATE = date(2026, 10, 24)
MODEL = "ecmwf_ifs"
LOCATION = {"latitude": 55.7558, "longitude": 37.6173}
ENDPOINT = "https://single-runs-api.open-meteo.com/v1/forecast"
SNAPSHOT = Path(__file__).with_name("samples") / f"open_meteo_{DEMO_DATE}.json"
HOUR_SECONDS = 3600


@dataclass(frozen=True)
class Weather:
    model_run_utc: str
    request_url: str
    mean_temperature_c: float
    precipitation_mm: float
    hours_with_precipitation: int


def weather_for_day(target: date, directory: Path, fetch: bool) -> tuple[Weather, str]:
    # Use the 00:00 UTC run on D-1 to allow for publication before 20:00 MSK.
    run = datetime.combine(target - timedelta(days=1), time.min, tzinfo=timezone.utc)
    params = {
        **LOCATION,
        "models": MODEL,
        "hourly": "temperature_2m,precipitation",
        "forecast_days": 2,
        "timeformat": "unixtime",
        "run": run.strftime("%Y-%m-%dT%H:%M"),
    }
    url = f"{ENDPOINT}?{urlencode(params)}"
    cached = directory / f"{MODEL}_{target}.json"
    if cached.is_file() and not fetch:
        raw = cached.read_text(encoding="utf-8")
        origin = "cache"
    elif target == DEMO_DATE and not fetch:
        raw = SNAPSHOT.read_text(encoding="utf-8")
        origin = "snapshot"
    else:
        raw = download(url)
        origin = "api"

    temperature, precipitation = daily_values(raw, target)
    if origin != "cache":
        write_text(cached, raw)
    weather = Weather(
        model_run_utc=run.isoformat(),
        request_url=url,
        mean_temperature_c=round(fmean(temperature), 2),
        precipitation_mm=round(sum(precipitation), 2),
        hours_with_precipitation=sum(value > 0 for value in precipitation),
    )
    return weather, origin


def download(url: str) -> str:
    for default_delay in (1, 2, 0):
        delay = default_delay
        try:
            with urlopen(url, timeout=10) as response:
                return response.read().decode("utf-8")
        except HTTPError as error:
            if not delay or (error.code not in (408, 429) and not 500 <= error.code < 600):
                raise
            retry_after = error.headers.get("Retry-After")
            if retry_after:
                try:
                    if retry_after.isdecimal():
                        requested_delay = int(retry_after)
                    else:
                        retry_at = parsedate_to_datetime(retry_after)
                        requested_delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    requested_delay = delay
                delay = max(delay, requested_delay)
            if delay > 20:
                raise
            error.close()
        except (URLError, TimeoutError, ConnectionError, IncompleteRead) as error:
            if not delay:
                raise OSError(f"Не удалось загрузить выпуск Open-Meteo: {error}") from error
        sleep(delay)
    raise RuntimeError("Не удалось получить выпуск Open-Meteo")


def hourly_rows(raw: str, suffix: str = "") -> dict[int, tuple[float | None, float | None]]:
    temperature_key = f"temperature_2m{suffix}"
    precipitation_key = f"precipitation{suffix}"
    try:
        payload = json.loads(raw)
        units = payload["hourly_units"]
        if (
            units["time"] != "unixtime"
            or units[temperature_key] != "°C"
            or units[precipitation_key] != "mm"
        ):
            raise ValueError("неожиданные единицы измерения")
        hourly = payload["hourly"]
        timestamps = hourly["time"]
        if any(type(timestamp) is not int for timestamp in timestamps):
            raise ValueError("неверный формат времени")
        rows = {
            timestamp: (temperature, precipitation)
            for timestamp, temperature, precipitation in zip(
                timestamps, hourly[temperature_key], hourly[precipitation_key], strict=True
            )
        }
        if len(rows) != len(timestamps):
            raise ValueError("дубликаты почасовых отметок")

        if any(
            value is not None and (type(value) not in (int, float) or not math.isfinite(value))
            for pair in rows.values()
            for value in pair
        ):
            raise ValueError("неверное почасовое значение")
        if any(value is not None and value < 0 for _, value in rows.values()):
            raise ValueError("отрицательное количество осадков")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Некорректный ответ Open-Meteo: {error}") from error
    return rows


def daily_slots(
    rows: dict[int, tuple[float | None, float | None]], target: date
) -> tuple[list[float | None], list[float | None]]:
    start = int(datetime.combine(target, time.min, tzinfo=MOSCOW).timestamp())
    hours = range(start, start + 24 * HOUR_SECONDS, HOUR_SECONDS)
    temperature = [rows.get(hour, (None, None))[0] for hour in hours]
    # Precipitation at a timestamp describes the preceding hour.
    precipitation = [rows.get(hour + HOUR_SECONDS, (None, None))[1] for hour in hours]
    return temperature, precipitation


def daily_values(raw: str, target: date) -> tuple[list[float], list[float]]:
    temperature, precipitation = daily_slots(hourly_rows(raw), target)
    valid_temperature = [value for value in temperature if value is not None]
    valid_precipitation = [value for value in precipitation if value is not None]
    if len(valid_temperature) != 24 or len(valid_precipitation) != 24:
        raise ValueError(f"Пропуски погоды Open-Meteo в пределах суток {target}")
    return valid_temperature, valid_precipitation
