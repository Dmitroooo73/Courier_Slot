"""Reproduce saved inputs and document feature ablation on validation only."""
import argparse
from datetime import date
from pathlib import Path

from backend.pipeline.orders import LEGACY_SOURCE
from backend.pipeline.results import EVALUATIONS, result_store
from backend.pipeline.storage import write_json
from backend.pipeline.weather_archive import WEATHER_FEATURES
from .evaluation import WINDOW_DAYS, VALIDATION_WINDOWS, fit_model, order_counts, score, split
from .features import ORDER_FEATURES
from .report import dataset_digest, evaluate_report, model_parameters, model_versions, report_inputs


def feature_ablation(frame) -> list[dict]:
    dates = sorted(frame["date"].unique())
    development = dates[-WINDOW_DAYS * (VALIDATION_WINDOWS + 1):-WINDOW_DAYS]
    folds = [split(frame, development[i], development[i + WINDOW_DAYS - 1])
             for i in range(0, len(development), WINDOW_DAYS)]
    results = []
    for label, excluded in (
        ("all", ()), ("without_weather", WEATHER_FEATURES),
        ("without_order_history", ORDER_FEATURES), ("without_weekday", ("weekday",)),
    ):
        actual, predicted = [], []
        for train, valid in folds:
            estimator, columns = fit_model("ridge", True, train, excluded=excluded)
            actual.extend(valid["actual_orders"].tolist())
            predicted.extend(order_counts(estimator.predict(valid[columns])))
        results.append({"configuration": label, "excluded": list(excluded), **score(actual, predicted)})
    for row in results:
        row["mae_delta_from_all"] = row["mae_orders"] - results[0]["mae_orders"]
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка воспроизводимости и вклада групп признаков")
    parser.add_argument("--date", type=date.fromisoformat,
                        help="архивный отчёт обучения; без даты — обучить заново на текущей версии данных")
    parser.add_argument("--target", type=date.fromisoformat, help="без --date: отсечка обучения (по умолчанию ближайший прогноз)")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("docs/model-results.json"))
    parser.add_argument("--save-quality", action="store_true", help="добавить проверенные сведения о качестве в сохранённый отчёт")
    args = parser.parse_args()
    store = result_store(args.data_dir)
    journal: dict = {}
    if args.date is None:
        from datetime import datetime

        from backend.daily import latest_due
        from backend.pipeline.orders import MOSCOW

        target = args.target or latest_due(datetime.now(MOSCOW))
        frame, future, weather = report_inputs(target, args.data_dir, journal)
        report, _ = evaluate_report(target, frame, future, weather, journal)
    else:
        report = store.read(EVALUATIONS, args.date)
        if report.get("forecast_mode") == "saved_model":
            parser.error("Для аудита обучающей выборки выберите исходный отчёт обучения или запустите без --date.")
        frame, _, _ = report_inputs(args.date, args.data_dir, journal, source=report.get("orders_source", LEGACY_SOURCE))
        if (dataset_digest(frame) != report["dataset_sha256"] or model_versions() != report["versions"]
                or model_parameters() != report["parameters"]):
            raise ValueError("Данные, библиотеки или параметры отличаются от сохранённого отчёта.")
    write_json(args.output, {
        "target_date": report["target_date"], "orders_source": report["orders_source"],
        "dataset_sha256": report["dataset_sha256"],
        "seed": report["seed"], "versions": report["versions"], "parameters": report["parameters"],
        "selected": report["selected"], "validation": report["validation"],
        "holdout": {key: value for key, value in report["holdout"].items() if key != "predictions"},
        "data_quality": journal["data_quality"], "ridge_feature_ablation": feature_ablation(frame),
    })
    if args.save_quality and args.date is not None:
        from backend.daily import result_lock
        with result_lock(store, args.data_dir):
            if store.read(EVALUATIONS, args.date) != report:
                raise ValueError("Отчёт изменился во время проверки. Повторите аудит.")
            store.publish(EVALUATIONS, args.date, {**report, "data_quality": journal["data_quality"]})
    print(f"Версия данных подтверждена. Результаты: {args.output}")


if __name__ == "__main__":
    main()
