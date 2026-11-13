"""Budgeted hyperparameter search with an experiment journal.

    python -m backend.modeling.tuning --trials 12 --seconds 300 --seed 42
    python -m backend.modeling.tuning --reproduce <experiment id>

Trials are scored on the same three validation windows as model selection. The last 28 days
take no part in the search: only the best configuration is checked on them, once.
"""
import argparse
import math
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from random import Random
from uuid import uuid4

from backend.pipeline.orders import MOSCOW, SOURCE

from .report import dataset_digest, report_inputs


SEARCH_VERSION = "random-search-v1"
SPACE = {
    "ridge": {"alpha": ("log", 0.01, 100.0)},
    "catboost": {"depth": ("choice", [3, 4, 5, 6, 7]), "learning_rate": ("log", 0.02, 0.2),
                 "iterations": ("choice", [150, 250, 400]), "l2_leaf_reg": ("choice", [1, 3, 10])},
}


def defaults() -> list[dict]:
    from .evaluation import CATBOOST_PARAMETERS, RIDGE_PARAMETERS

    catboost = {key: CATBOOST_PARAMETERS[key] for key in ("depth", "learning_rate", "iterations")} | {"l2_leaf_reg": 3}
    return [{"model": "ridge", "uses_weather": True, "params": dict(RIDGE_PARAMETERS), "origin": "default"},
            {"model": "catboost", "uses_weather": True, "params": catboost, "origin": "default"}]


def sample(rng: Random) -> dict:
    model = rng.choice(sorted(SPACE))
    params = {}
    for name, (kind, *spec) in SPACE[model].items():
        if kind == "log":
            low, high = spec
            params[name] = float(f"{10 ** rng.uniform(math.log10(low), math.log10(high)):.4g}")
        else:
            params[name] = rng.choice(spec[0])
    return {"model": model, "uses_weather": rng.random() < 0.5, "params": params, "origin": "random"}


def folds(frame):
    from .evaluation import VALIDATION_WINDOWS, WINDOW_DAYS, split

    dates = sorted(frame["date"].unique())
    development = dates[-WINDOW_DAYS * (VALIDATION_WINDOWS + 1):-WINDOW_DAYS]
    return [split(frame, development[offset], development[offset + WINDOW_DAYS - 1])
            for offset in range(0, len(development), WINDOW_DAYS)]


def holdout_split(frame):
    from .evaluation import WINDOW_DAYS, split

    dates = sorted(frame["date"].unique())
    return split(frame, dates[-WINDOW_DAYS], dates[-1])


def predict_config(config: dict, train, test) -> list[int]:
    from .evaluation import fit_model, order_counts

    estimator, columns = fit_model(config["model"], config["uses_weather"], train, params=config["params"])
    return order_counts(estimator.predict(test[columns]))


def score_config(config: dict, windows) -> dict:
    from .evaluation import score

    actual, predicted = [], []
    for train, valid in windows:
        actual.extend(valid["actual_orders"].tolist())
        predicted.extend(predict_config(config, train, valid))
    return score(actual, predicted)


def search(frame, *, trials: int, seconds: float, seed: int, clock=time.monotonic) -> dict:
    """Defaults first, then random configurations until the trial or time budget ends."""
    windows = folds(frame)
    rng = Random(seed)
    started = clock()
    results = []
    queue = defaults()
    while len(results) < trials and clock() - started < seconds:
        config = queue.pop(0) if queue else sample(rng)
        trial_started = clock()
        metrics = score_config(config, windows)
        results.append({"trial": len(results) + 1, **config, **metrics, "seconds": round(clock() - trial_started, 2)})
    if not results:
        raise ValueError("Бюджет не позволил провести ни одного испытания.")
    best = min(results, key=lambda row: row["mae_orders"])
    default = min((row for row in results if row["origin"] == "default"), key=lambda row: row["mae_orders"])
    return {"trials": results, "best_trial": best["trial"], "default_trial": default["trial"],
            "spent_seconds": round(clock() - started, 2), "holdout": check_holdout(frame, best, default)}


def check_holdout(frame, best: dict, default: dict) -> dict:
    from .evaluation import score

    train, holdout = holdout_split(frame)
    actual = holdout["actual_orders"].tolist()
    return {"start": holdout["date"].min().isoformat(), "end": holdout["date"].max().isoformat(),
            "train_through": train["date"].max().isoformat(),
            "best": score(actual, predict_config(best, train, holdout)),
            "default": score(actual, predict_config(default, train, holdout)),
            "baseline": score(actual, holdout["orders_lag_7"].astype(int).tolist())}


def run_experiment(frame, *, target: date, trials: int, seconds: float, seed: int, now: datetime) -> dict:
    from .registry import runtime_versions

    result = search(frame, trials=trials, seconds=seconds, seed=seed)
    return {
        "experiment_id": f"exp-{now.astimezone(timezone.utc):%Y%m%dT%H%M%S}-{uuid4().hex[:6]}",
        "created_at_utc": now.astimezone(timezone.utc).isoformat(),
        "search": SEARCH_VERSION, "target_date": target.isoformat(), "seed": seed,
        "budget": {"max_trials": trials, "max_seconds": seconds},
        "data": {"orders_source": SOURCE, "dataset_sha256": dataset_digest(frame), "rows": len(frame),
                 "start": frame["date"].min().isoformat(), "end": frame["date"].max().isoformat()},
        "space": {model: {name: list(spec) for name, spec in space.items()} for model, space in SPACE.items()},
        "versions": runtime_versions(),
        **result,
    }


def reproduce(experiment: dict, frame) -> list[str]:
    """Re-run every recorded trial and the holdout check; return the differences (none means reproduced)."""
    problems = []
    if dataset_digest(frame) != experiment["data"]["dataset_sha256"]:
        return ["версия данных (SHA-256) отличается"]
    windows = folds(frame)
    for trial in experiment["trials"]:
        metrics = score_config(trial, windows)
        for key in ("mae_orders", "wape_percent", "observations"):
            if metrics[key] != trial[key]:
                problems.append(f"испытание {trial['trial']}: {key}")
    trials = {row["trial"]: row for row in experiment["trials"]}
    holdout = check_holdout(frame, trials[experiment["best_trial"]], trials[experiment["default_trial"]])
    if holdout != experiment["holdout"]:
        problems.append("проверка на отложенных днях")
    rng = Random(experiment["seed"])
    expected = [sample(rng) for row in experiment["trials"] if row["origin"] == "random"]
    recorded = [{key: row[key] for key in ("model", "uses_weather", "params", "origin")}
                for row in experiment["trials"] if row["origin"] == "random"]
    if expected != recorded:
        problems.append("последовательность конфигураций для этого seed")
    return problems


def describe(config: dict) -> str:
    name = "Ridge" if config["model"] == "ridge" else "CatBoost" if config["model"] == "catboost" else config["model"]
    params = ", ".join(f"{key} {format(value, '.4g').replace('.', ',')}" for key, value in config["params"].items())
    return f"{name} {'с погодой' if config['uses_weather'] else 'без погоды'} ({params})"


def main() -> int:
    from backend.daily import DEFAULT_DATA, latest_due, result_lock
    from backend.pipeline.results import result_store

    parser = argparse.ArgumentParser(description="Подбор гиперпараметров с ограниченным бюджетом и журнал экспериментов")
    parser.add_argument("--trials", type=int, default=12, help="наибольшее число испытаний")
    parser.add_argument("--seconds", type=float, default=300, help="наибольшее время поиска")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--date", type=date.fromisoformat, help="данные до отсечки этой даты (по умолчанию — ближайший прогноз)")
    parser.add_argument("--reproduce", metavar="EXPERIMENT_ID", help="повторить эксперимент из журнала")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    args = parser.parse_args()
    store = result_store(args.data_dir)
    if args.reproduce:
        experiment = store.experiment(args.reproduce)
        with result_lock(store, args.data_dir):
            frame, _, _ = report_inputs(date.fromisoformat(experiment["target_date"]), args.data_dir, {},
                                        source=experiment["data"]["orders_source"])
        problems = reproduce(experiment, frame)
        if problems:
            print("Не воспроизвелось: " + "; ".join(problems), file=sys.stderr)
            return 1
        print(f"OK: {experiment['experiment_id']} — {len(experiment['trials'])} испытаний, отложенная проверка "
              "и последовательность seed совпали.")
        return 0
    target = args.date or latest_due(datetime.now(MOSCOW))
    with result_lock(store, args.data_dir):
        frame, _, _ = report_inputs(target, args.data_dir, {})
    experiment = run_experiment(frame, target=target, trials=args.trials, seconds=args.seconds, seed=args.seed,
                                now=datetime.now(timezone.utc))
    store.save_experiment(experiment)
    trials = {row["trial"]: row for row in experiment["trials"]}
    best, holdout = trials[experiment["best_trial"]], experiment["holdout"]
    print(f"Эксперимент {experiment['experiment_id']}: {len(trials)} испытаний за {experiment['spent_seconds']} с.")
    for row in sorted(trials.values(), key=lambda item: item["mae_orders"]):
        print(f"  {row['trial']:>2}. {describe(row):<70} MAE {row['mae_orders']:.3f}")
    print(f"Лучший: {describe(best)}. На отложенных днях MAE {holdout['best']['mae_orders']:.3f}, "
          f"по умолчанию {holdout['default']['mae_orders']:.3f}, база {holdout['baseline']['mae_orders']:.3f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
