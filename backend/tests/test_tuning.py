import unittest
from datetime import date, datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.pipeline.orders import ZONES, synthetic_records
from backend.pipeline.results import FileResults
from backend.tests.weather_stub import observed_stub


def frame_for(days: int = 150):
    from backend.modeling.report import training_frame

    last = date(2026, 6, 30)
    first = last - timedelta(days=days)
    weather = observed_stub(first, last, None)
    history = {}
    for day in weather:
        counts = dict.fromkeys((zone.id for zone in ZONES), 0)
        for _, _, zone in synthetic_records(day, weather[day]):
            counts[zone] += 1
        history[day] = counts
    return training_frame(history, weather, first + timedelta(days=14))


@patch.dict("os.environ", {"PULT_DATABASE_URL": ""})
@patch.dict("backend.modeling.evaluation.CATBOOST_PARAMETERS", {"iterations": 30})
class TuningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = frame_for()

    def test_seeded_search_respects_budget_and_never_touches_holdout(self):
        from backend.modeling.evaluation import WINDOW_DAYS
        from backend.modeling.tuning import SPACE, search

        with patch("backend.modeling.tuning.SPACE", {**SPACE, "catboost": {**SPACE["catboost"], "iterations": ("choice", [20, 30])}}):
            first = search(self.frame, trials=4, seconds=600, seed=7)
            again = search(self.frame, trials=4, seconds=600, seed=7)
            self.assertEqual([row["origin"] for row in first["trials"]], ["default", "default", "random", "random"])
            self.assertEqual([{k: v for k, v in row.items() if k != "seconds"} for row in first["trials"]],
                             [{k: v for k, v in row.items() if k != "seconds"} for row in again["trials"]])

            # Changing only the last 28 days must not move any trial, only the final check.
            changed = self.frame.copy()
            holdout = changed["date"] >= sorted(changed["date"].unique())[-WINDOW_DAYS]
            changed.loc[holdout, "actual_orders"] += 50
            shifted = search(changed, trials=4, seconds=600, seed=7)
            self.assertEqual([row["mae_orders"] for row in shifted["trials"]], [row["mae_orders"] for row in first["trials"]])
            self.assertNotEqual(shifted["holdout"]["best"], first["holdout"]["best"])

            ticks = count(0, 10)
            limited = search(self.frame, trials=50, seconds=25, seed=7, clock=lambda: next(ticks))
            self.assertLess(len(limited["trials"]), 50)
            with self.assertRaisesRegex(ValueError, "Бюджет"):
                search(self.frame, trials=5, seconds=0, seed=7)

    def test_journal_entry_reproduces_by_id_and_detects_tampering(self):
        from backend.modeling.tuning import reproduce, run_experiment

        experiment = run_experiment(self.frame, target=date(2026, 7, 2), trials=3, seconds=600, seed=3,
                                    now=datetime(2026, 7, 1, 18, tzinfo=timezone.utc))
        self.assertEqual(experiment["data"]["rows"], len(self.frame))
        self.assertIn(experiment["best_trial"], [row["trial"] for row in experiment["trials"]])
        with TemporaryDirectory() as directory:
            store = FileResults(Path(directory))
            store.save_experiment(experiment)
            with self.assertRaisesRegex(ValueError, "уже записан"):
                store.save_experiment(experiment)
            saved = store.experiment(experiment["experiment_id"])
        self.assertEqual(reproduce(saved, self.frame), [])
        tampered = {**saved, "trials": [{**saved["trials"][0], "mae_orders": 0.0}, *saved["trials"][1:]]}
        self.assertEqual(reproduce(tampered, self.frame), ["испытание 1: mae_orders"])
        self.assertEqual(reproduce(saved, self.frame.iloc[6:]), ["версия данных (SHA-256) отличается"])
