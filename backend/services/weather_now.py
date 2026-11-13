"""Current Moscow weather for display only. It never feeds the model: forecasts use archived runs."""
import json
from datetime import datetime, timedelta, timezone
from os import getenv
from threading import Lock
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from backend.pipeline.weather import LOCATION


ENDPOINT = "https://api.open-meteo.com/v1/forecast"
CACHE_FOR = timedelta(minutes=10)
TIMEOUT_SECONDS = 4
CODES = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "туман с изморозью", 51: "слабая морось", 53: "морось", 55: "сильная морось",
    56: "ледяная морось", 57: "ледяная морось", 61: "слабый дождь", 63: "дождь", 65: "сильный дождь",
    66: "ледяной дождь", 67: "ледяной дождь", 71: "слабый снег", 73: "снег", 75: "сильный снег",
    77: "снежная крупа", 80: "ливень", 81: "ливень", 82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "гроза с градом",
}


class WeatherUnavailable(RuntimeError):
    pass


_lock = Lock()
_cached: dict | None = None
_cached_at: datetime | None = None


def request_url() -> str:
    query = {**LOCATION, "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
             "wind_speed_unit": "ms", "timezone": "Europe/Moscow"}
    return f"{ENDPOINT}?{urlencode(query)}"


def parse(raw: bytes, fetched: datetime) -> dict:
    current = json.loads(raw)["current"]
    code = current.get("weather_code")
    values = {
        "temperature_c": current["temperature_2m"],
        "apparent_c": current.get("apparent_temperature"),
        "precipitation_mm": current.get("precipitation"),
        "wind_ms": current.get("wind_speed_10m"),
        "weather_code": code,
        "description": CODES.get(code, "без описания"),
        "observed_at": current["time"] + "+03:00",
        "fetched_at_utc": fetched.isoformat(),
        "source": "Open-Meteo",
    }
    if not isinstance(values["temperature_c"], (int, float)):
        raise ValueError("нет температуры")
    return values


def current_weather(now: datetime | None = None, fetch=urlopen) -> dict:
    """Serve one upstream request per 10 minutes; on failure keep showing the last value, marked stale."""
    global _cached, _cached_at
    now = now or datetime.now(timezone.utc)
    if getenv("PULT_LIVE_WEATHER", "1") == "0":
        raise WeatherUnavailable("Показ текущей погоды отключён настройкой PULT_LIVE_WEATHER=0.")
    with _lock:
        if _cached is not None and _cached_at is not None and now - _cached_at < CACHE_FOR:
            return {**_cached, "stale": False}
        try:
            request = Request(request_url(), headers={"User-Agent": "pult-dostavki/1.0"})
            with fetch(request, timeout=TIMEOUT_SECONDS) as response:
                _cached = parse(response.read(), now)
            _cached_at = now
            return {**_cached, "stale": False}
        except (OSError, URLError, ValueError, KeyError, TypeError) as error:
            if _cached is not None:
                return {**_cached, "stale": True}
            raise WeatherUnavailable("Open-Meteo сейчас не отвечает. Прогноз заказов от этого не зависит.") from error


def reset_cache() -> None:
    global _cached, _cached_at
    with _lock:
        _cached = _cached_at = None
