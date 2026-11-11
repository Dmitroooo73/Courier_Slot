import os
from datetime import date
from pathlib import Path

from backend.daily import result_lock
from backend.pipeline.results import EVALUATIONS, result_store
from backend.pipeline.weather_archive import WEATHER_FEATURES

from .evaluation import forecast_details
from .report import dataset_digest, model_parameters, model_versions, report_inputs


def reproduce_scenarios(target: date, data: Path, report: dict) -> dict | None:
    if report.get("versions") != model_versions() or report.get("parameters") != model_parameters():
        return None
    frame, future, weather = report_inputs(target, data, {})
    if dataset_digest(frame) != report.get("dataset_sha256") or any(
        report["weather"].get(feature) != weather[target][feature] for feature in WEATHER_FEATURES
    ):
        return None
    counts, _, scenarios = forecast_details(**report["selected"], train=frame, test=future, include_scenarios=True)
    published = {row["zone_id"]: row["forecast_orders"] for row in report["forecasts"]}
    return scenarios if dict(zip(future["zone_id"], counts, strict=True)) == published else None


def main() -> None:
    data = Path(os.getenv("PULT_DATA_DIR", "data"))
    store = result_store(data)
    with result_lock(store, data):
        for target in store.dates():
            if not store.has(EVALUATIONS, target):
                continue
            report = store.read(EVALUATIONS, target)
            if "scenarios" in report:
                continue
            scenarios = reproduce_scenarios(target, data, report)
            if scenarios is None:
                print(f"{target} — исходные данные или версии модели отличаются.", flush=True)
                continue
            store.publish(EVALUATIONS, target, {**report, "scenarios": scenarios})
            print(f"{target} — сценарии добавлены к опубликованному прогнозу.", flush=True)


if __name__ == "__main__":
    main()
