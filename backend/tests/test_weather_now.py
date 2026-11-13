import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.error import URLError

from backend.services import weather_now
from backend.services.weather_now import WeatherUnavailable, current_weather


NOW = datetime(2026, 11, 13, 13, 40, tzinfo=timezone.utc)
BODY = {"current": {"time": "2026-11-13T16:30", "temperature_2m": 3.4, "apparent_temperature": 0.1,
                    "precipitation": 0.2, "weather_code": 61, "wind_speed_10m": 4.2}}


class Upstream:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def __call__(self, request, timeout):
        self.calls += 1
        assert timeout <= 5 and "current=" in request.full_url
        if self.fail:
            raise URLError("offline")
        return io.BytesIO(json.dumps(BODY).encode())


@patch.dict("os.environ", {"PULT_LIVE_WEATHER": "1"})
class WeatherNowTests(unittest.TestCase):
    def setUp(self):
        weather_now.reset_cache()
        self.addCleanup(weather_now.reset_cache)

    def test_one_upstream_call_per_ten_minutes_and_stale_value_on_failure(self):
        upstream = Upstream()
        first = current_weather(NOW, fetch=upstream)
        self.assertEqual((first["temperature_c"], first["description"], first["stale"]), (3.4, "слабый дождь", False))
        self.assertEqual(first["observed_at"], "2026-11-13T16:30+03:00")
        current_weather(NOW + timedelta(minutes=9), fetch=upstream)
        self.assertEqual(upstream.calls, 1)

        upstream.fail = True
        stale = current_weather(NOW + timedelta(minutes=11), fetch=upstream)
        self.assertEqual((upstream.calls, stale["stale"], stale["temperature_c"]), (2, True, 3.4))

    def test_no_value_yet_or_disabled_is_an_explicit_error(self):
        upstream = Upstream()
        upstream.fail = True
        with self.assertRaisesRegex(WeatherUnavailable, "не отвечает"):
            current_weather(NOW, fetch=upstream)
        with patch.dict("os.environ", {"PULT_LIVE_WEATHER": "0"}), self.assertRaisesRegex(WeatherUnavailable, "отключён"):
            current_weather(NOW, fetch=upstream)
