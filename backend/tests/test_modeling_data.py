import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from backend.modeling.features import features_for_day
from backend.pipeline.orders import ZONES
from backend.pipeline.weather_archive import SUFFIX, utc_hours, weather_history


class ModelingDataTests(unittest.TestCase):
    def test_features_exclude_incomplete_day_and_target(self):
        target = date(2026, 10, 24)
        history = {
            target - timedelta(days=lag): {zone.id: lag for zone in ZONES}
            for lag in range(15)
        }
        expected = features_for_day(target, history, {})
        history[target]["east"] = 9999
        history[target - timedelta(days=1)]["east"] = 9999
        self.assertEqual(features_for_day(target, history, {}), expected)
        self.assertEqual(expected[0]["orders_lag_2"], 2)
        self.assertEqual(expected[0]["mean_closed_week"], 5)

    def test_weather_archive_reuses_dates_and_keeps_incomplete_days_missing(self):
        target = date(2026, 10, 24)
        previous = target - timedelta(days=1)
        tomorrow = target + timedelta(days=1)
        requested = []

        def mock_archive(url):
            query = parse_qs(urlsplit(url).query)
            start = date.fromisoformat(query["start_date"][0])
            end = date.fromisoformat(query["end_date"][0])
            requested.append((start, end))
            hours = [
                hour
                for offset in range((end - start).days + 1)
                for hour in utc_hours(start + timedelta(days=offset))
            ]
            return json.dumps(
                {
                    "hourly_units": {
                        "time": "unixtime",
                        f"temperature_2m{SUFFIX}": "°C",
                        f"precipitation{SUFFIX}": "mm",
                    },
                    "hourly": {
                        "time": hours,
                        f"temperature_2m{SUFFIX}": [
                            None if hour == utc_hours(previous)[21] else 10 for hour in hours
                        ],
                        f"precipitation{SUFFIX}": [0] * len(hours),
                    },
                }
            )

        with (
            TemporaryDirectory(dir=Path.cwd()) as directory,
            patch("backend.pipeline.weather_archive.download", side_effect=mock_archive),
        ):
            archive = Path(directory)
            first, fetched = weather_history(target, target, archive)
            self.assertEqual(fetched, 2)
            self.assertEqual(weather_history(target, target, archive), (first, 0))
            extended, fetched = weather_history(target, tomorrow, archive)
            self.assertEqual(fetched, 1)

        self.assertIsNone(first[target]["mean_temperature_c"])
        self.assertEqual(first[target]["precipitation_mm"], 0)
        self.assertEqual(extended[tomorrow]["mean_temperature_c"], 10)
        self.assertEqual(requested, [(previous, target), (tomorrow, tomorrow)])
