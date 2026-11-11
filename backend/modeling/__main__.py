import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from backend.pipeline.orders import ZONES
from backend.pipeline.results import EVALUATIONS, StorageUnavailable, result_store
from backend.pipeline.weather import DEMO_DATE

from .report import FIRST_MODEL_DATE, build_report


def run(target: date, data: Path, zone_id: str | None) -> None:
    report = build_report(target, data, {})
    store = result_store(data)
    store.publish(EVALUATIONS, target, report)

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
    print(f"\nПрогноз на {target}")
    for row in report["forecasts"]:
        if zone_id is None or row["zone_id"] == zone_id:
            print(
                f"{row['zone_name']:<14} прогноз {row['forecast_orders']}, "
                f"мощность {row['capacity_orders']}"
            )
    print(f"\nОтчёт: {store.location(EVALUATIONS, target)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Сравнение моделей на годе истории")
    parser.add_argument("--date", type=date.fromisoformat, default=DEMO_DATE, help="дата прогноза")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="каталог данных")
    parser.add_argument("--zone", choices=[zone.id for zone in ZONES], help="показать прогноз по выбранной зоне")
    args = parser.parse_args()
    if args.date < FIRST_MODEL_DATE:
        parser.error(f"для годовой выборки укажите дату с {FIRST_MODEL_DATE}")
    try:
        run(args.date, args.data_dir, args.zone)
    except ModuleNotFoundError as error:
        print(f"Установите зависимости из requirements-ml.txt. {error}", file=sys.stderr)
        return 1
    except (OSError, StorageUnavailable, ValueError, csv.Error) as error:
        print(f"Обучение прервано. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
