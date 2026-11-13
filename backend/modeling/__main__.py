import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path

from backend.daily import latest_due, result_lock
from backend.pipeline.orders import MOSCOW, ZONES
from backend.pipeline.results import result_store
from backend.pipeline.weather import DEMO_DATE

from .report import FIRST_MODEL_DATE, build_report
from .registry import make_model_version


def run(target: date, data: Path, zone_id: str | None) -> None:
    store = result_store(data)
    with result_lock(store, data):
        report, fitted = build_report(target, data, {})
        model = make_model_version(report, *fitted, status="registered")
        store.save_model(model)

    print(f"Версия в реестре: {model.metadata['version_id']}")

    print("Временная валидация · синтетические заказы · ошибка на зону в сутки")
    print(f"{'Модель':<20} {'Погода':<8} {'MAE':>8} {'WAPE':>9}")
    for row in report["validation"]:
        weather_label = "да" if row["uses_weather"] else "нет"
        print(f"{row['model']:<20} {weather_label:<8} {row['mae_orders']:>8.2f} {row['wape_percent']:>8.2f}%")
    chosen = report["selected"]
    holdout = report["holdout"]
    print(
        f"\nВыбрана {chosen['model']} ({'с погодой' if chosen['uses_weather'] else 'без погоды'}). "
        f"На отложенных датах MAE {holdout['selected']['mae_orders']:.2f}, "
        f"у базы {holdout['baseline']['mae_orders']:.2f}."
    )
    print(f"\nРасчёт на {target} для проверки")
    for row in report["forecasts"]:
        if zone_id is None or row["zone_id"] == zone_id:
            print(
                f"{row['zone_name']:<14} прогноз {row['forecast_orders']}, "
                f"мощность {row['capacity_orders']}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Сравнение моделей на годе истории")
    parser.add_argument("--date", type=date.fromisoformat, default=DEMO_DATE, help="дата прогноза")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="каталог данных")
    parser.add_argument("--zone", choices=[zone.id for zone in ZONES], help="показать прогноз по выбранной зоне")
    parser.add_argument("--register-only", action="store_true", help="сохранить модель в реестре (режим по умолчанию)")
    args = parser.parse_args()
    due = latest_due(datetime.now(MOSCOW))
    if not FIRST_MODEL_DATE <= args.date <= due:
        parser.error(f"дата прогноза должна быть в диапазоне {FIRST_MODEL_DATE} — {due}")
    try:
        run(args.date, args.data_dir, args.zone)
    except ModuleNotFoundError as error:
        print(f"Установите зависимости из requirements-ml.txt. {error}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError, csv.Error) as error:
        print(f"Обучение прервано. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
