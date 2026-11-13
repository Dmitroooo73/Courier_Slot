"""One-time provisioning for daily prediction. Replacing the version is a separate lifecycle action."""
import argparse
import re
from datetime import date, datetime, timezone
from pathlib import Path

from backend.daily import DEFAULT_DATA, latest_due, result_lock
from backend.pipeline.orders import MOSCOW
from backend.pipeline.results import EVALUATIONS, ModelVersion, result_store

from .lifecycle import active_context, adopt_version, train_first_version
from .registry import load_estimator
from .report import FIRST_MODEL_DATE, dataset_digest, model_parameters, report_inputs


def verify_existing(model: ModelVersion, report: dict, frame, future):
    from .evaluation import forecast_details

    metadata = model.metadata
    selected = {key: metadata[key] for key in ("model", "uses_weather")}
    training = {"start": frame["date"].min().isoformat(), "end": frame["date"].max().isoformat(), "rows": len(frame)}
    validation = next((row for row in report["validation"] if all(row[key] == value for key, value in selected.items())), {})
    if (report.get("selected") != selected or metadata["training"] != training
            or metadata["dataset_sha256"] != report.get("dataset_sha256") or dataset_digest(frame) != metadata["dataset_sha256"]
            or report["parameters"] != model_parameters() or metadata["parameters"] != report["parameters"][metadata["model"]]
            or any(validation.get(key) != value for key, value in metadata["validation"].items())
            or any(report["holdout"].get(key) != value for key, value in metadata["holdout"].items())):
        raise ValueError("Паспорт версии не совпадает с архивной проверкой и исходными данными.")
    estimator = load_estimator(model)
    counts, _, _ = forecast_details(**selected, train=frame, test=future, fitted=(estimator, metadata["features"]))
    published = {row["zone_id"]: row["forecast_orders"] for row in report["forecasts"]}
    if dict(zip(future["zone_id"], counts, strict=True)) != published:
        raise ValueError("Сохранённая модель не воспроизвела архивный прогноз.")
    return estimator


def initialize(data: Path, *, version_id: str | None = None, target: date | None = None) -> str:
    if version_id is not None and not re.fullmatch(r"[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}", version_id):
        raise ValueError("Некорректный идентификатор версии модели.")
    store = result_store(data)
    with result_lock(store, data):
        if (current := store.active_model()) is not None:
            if version_id == current["version_id"]:
                return version_id
            raise ValueError("Рабочая модель уже настроена.")

        now = datetime.now(timezone.utc)
        if version_id is not None:
            model = store.model_version(version_id)
            target = date.fromisoformat(model.metadata["target_date"])
            report = store.read(EVALUATIONS, target)
            frame, future, _ = report_inputs(target, data, {})
            estimator = verify_existing(model, report, frame, future)
            return adopt_version(store, model, active_context(model, estimator, frame, report), now, actor="command")
        if target is None or not FIRST_MODEL_DATE <= target <= latest_due(datetime.now(MOSCOW)):
            raise ValueError("Укажите доступную дату для первой рабочей модели.")
        return train_first_version(store, data, target, now, actor="command")


def main() -> None:
    parser = argparse.ArgumentParser(description="Настроить первую рабочую модель для ежедневного прогноза")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--version", help="версия из реестра с совпадающим архивным прогнозом")
    source.add_argument("--train-for", type=date.fromisoformat, help="обучить первую модель на данных до отсечки этой даты")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    args = parser.parse_args()
    try:
        version_id = initialize(args.data_dir, version_id=args.version, target=args.train_for)
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"Настройка рабочей модели прервана. {error}\n")
    print(f"Рабочая версия: {version_id}")


if __name__ == "__main__":
    main()
