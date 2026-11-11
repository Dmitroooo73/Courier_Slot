import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import call, patch
from urllib.error import HTTPError

from backend.pipeline.__main__ import run
from backend.pipeline.weather import DEMO_DATE, SNAPSHOT, daily_values, download


@patch.dict("os.environ", {"PULT_DATABASE_URL": ""})
class DemoTests(unittest.TestCase):
    def test_repeat_ignores_orders_after_cutoff(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory, redirect_stdout(io.StringIO()):
            data = Path(directory)
            run(DEMO_DATE, data, fetch_weather=False)
            orders = data / "raw" / "orders"
            first_orders = {path.name: path.read_bytes() for path in orders.glob("*.csv")}
            self.assertEqual(len(first_orders), 14)
            forecast = data / "forecasts" / f"{DEMO_DATE}.json"
            first_forecast = forecast.read_bytes()

            later = DEMO_DATE - timedelta(days=1)
            later_file = orders / f"{later}.csv"
            later_file.write_text("data outside the forecast window", encoding="utf-8")
            run(DEMO_DATE, data, fetch_weather=False)

            self.assertEqual(forecast.read_bytes(), first_forecast)
            self.assertEqual(
                {path.name: path.read_bytes() for path in orders.glob("*.csv") if path != later_file},
                first_orders,
            )
            log = json.loads(max((data / "runs").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(log["orders"]["generated_days"], 0)
            self.assertEqual(log["orders"]["reused_days"], 14)
            self.assertEqual(log["weather"]["origin"], "cache")

    def test_invalid_orders_keep_previous_forecast(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory, redirect_stdout(io.StringIO()):
            data = Path(directory)
            run(DEMO_DATE, data, fetch_weather=False)
            forecast = data / "forecasts" / f"{DEMO_DATE}.json"
            first_forecast = forecast.read_bytes()
            reference = DEMO_DATE - timedelta(days=7)
            raw_orders = data / "raw" / "orders" / f"{reference}.csv"
            with raw_orders.open("r+", encoding="utf-8") as source:
                _, first_order, *_ = source.readlines()
                source.write(first_order)

            with self.assertRaisesRegex(ValueError, "дубликат"):
                run(DEMO_DATE, data, fetch_weather=False)
            self.assertEqual(forecast.read_bytes(), first_forecast)
            log = json.loads(max((data / "runs").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(log["status"], "failed")
            self.assertEqual(log["step"], "orders")
            self.assertEqual(log["last_forecast_file"], f"forecasts/{DEMO_DATE}.json")

    def test_precipitation_uses_hour_ending_at_next_local_midnight(self):
        raw = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        precipitation = raw["hourly"]["precipitation"]
        # The target day starts at index 21 in the 00:00 UTC run.
        precipitation[21] = 50
        precipitation[45] = 2
        temperature, precipitation = daily_values(json.dumps(raw), DEMO_DATE)
        self.assertEqual(len(temperature), 24)
        self.assertEqual(len(precipitation), 24)
        self.assertEqual(sum(precipitation), 2)

    def test_download_retries_rate_limit_and_timeout(self):
        url = "https://example.com"
        rate_limit = HTTPError(url, 429, "Too Many Requests", {"Retry-After": "3"}, None)
        responses = [rate_limit, TimeoutError(), io.BytesIO(b"{}")]
        with (
            patch("backend.pipeline.weather.urlopen", side_effect=responses) as request,
            patch("backend.pipeline.weather.sleep") as wait,
        ):
            self.assertEqual(download(url), "{}")
        self.assertEqual(request.call_count, 3)
        self.assertEqual(wait.call_args_list, [call(3), call(2)])
