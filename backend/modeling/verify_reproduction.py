"""Rebuild a saved training report or a registered model version from freshly generated event logs.

    python -m backend.modeling.verify_reproduction --date 2026-11-11       # archived training report
    python -m backend.modeling.verify_reproduction --version <version id>  # any version in the registry
Weather comes from the cached public responses; orders are generated again in a temporary folder.
"""
import argparse
from datetime import date
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory

from backend.pipeline.orders import LEGACY_SOURCE, orders_directory
from backend.pipeline.results import EVALUATIONS, result_store
from .report import build_report, dataset_digest, evaluate_report, report_inputs


def fresh_folder(source: Path, temporary: str) -> Path:
    data = Path(temporary)
    copytree(source / "raw" / "weather", data / "raw" / "weather")
    return data


def verify_report(target: date, source_dir: Path, saved: dict) -> None:
    source = saved.get("orders_source", LEGACY_SOURCE)
    with TemporaryDirectory(prefix="pult-reproduction-") as temporary:
        data = fresh_folder(source_dir, temporary)
        rebuilt, _ = build_report(target, data, {}, source=source)
        for key in ("dataset_sha256", "selected", "forecasts", "validation", "holdout", "scenarios", "parameters", "versions"):
            if rebuilt[key] != saved[key]:
                raise ValueError(f"Воспроизведение не совпало: {key}")
        logs = list((orders_directory(data / "raw", source).parent / "order_events").glob("*.log"))
        if len(logs) != 365:
            raise ValueError("Создана неполная история событий")
        repeated: dict = {}
        frame, _, _ = report_inputs(target, data, repeated, source=source)
        if repeated["orders"]["generated_days"] != 0 or dataset_digest(frame) != saved["dataset_sha256"]:
            raise ValueError("Повторная загрузка не идемпотентна")
    print(f"OK: 365 журналов событий ({source}) → CSV → признаки → модели → прежние прогнозы и метрики.")
    print("OK: повторный запуск использует кэш, версия данных не меняется.")


def verify_version(metadata: dict, source_dir: Path) -> None:
    from .evaluation import order_counts
    from .registry import load_estimator

    target = date.fromisoformat(metadata["target_date"])
    source = metadata["orders_source"]
    training = metadata["training"]
    with TemporaryDirectory(prefix="pult-reproduction-") as temporary:
        data = fresh_folder(source_dir, temporary)
        journal: dict = {}
        frame, future, weather = report_inputs(target, data, journal, source=source)
        dates = frame["date"].map(lambda day: day.isoformat())
        frame = frame[dates.between(training["start"], training["end"])].reset_index(drop=True)
        if dataset_digest(frame) != metadata["dataset_sha256"]:
            raise ValueError("Воспроизведение не совпало: dataset_sha256")
        report, fitted = evaluate_report(target, frame, future, weather, journal, source=source,
                                         half_life=metadata["parameters"].get("recency_half_life_days"))
        selected = {"model": metadata["model"], "uses_weather": metadata["uses_weather"]}
        if report["selected"] != selected or report["parameters"][metadata["model"]] != metadata["parameters"]:
            raise ValueError("Воспроизведение не совпало: выбранная модель или параметры")
        validation = next(row for row in report["validation"] if all(row[key] == value for key, value in selected.items()))
        for key, value in metadata["validation"].items():
            if validation[key] != value:
                raise ValueError(f"Воспроизведение не совпало: валидация {key}")
        for key, value in metadata["holdout"].items():
            if report["holdout"][key] != value:
                raise ValueError(f"Воспроизведение не совпало: отложенная проверка {key}")
        if fitted[0] is not None:
            saved_estimator = load_estimator(result_store(source_dir).model_version(metadata["version_id"]))
            columns = metadata["features"]
            if order_counts(fitted[0].predict(frame[columns])) != order_counts(saved_estimator.predict(frame[columns])):
                raise ValueError("Воспроизведение не совпало: прогнозы обученной модели")
    print(f"OK: версия {metadata['version_id']} обучена заново на {len(frame)} строках {source}; "
          "данные, выбор модели, метрики и прогнозы совпали.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка полного прогона на заново созданных заказах")
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--date", type=date.fromisoformat, help="архивный отчёт, созданный при обучении")
    what.add_argument("--version", help="идентификатор версии модели из реестра")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    store = result_store(args.data_dir)
    if args.version:
        verify_version(store.model_metadata(args.version), args.data_dir)
        return
    saved = store.read(EVALUATIONS, args.date)
    if saved.get("forecast_mode") == "saved_model":
        parser.error("Этот прогноз считала сохранённая модель. Укажите --version из реестра или архивный отчёт обучения.")
    verify_report(args.date, args.data_dir, saved)


if __name__ == "__main__":
    main()
