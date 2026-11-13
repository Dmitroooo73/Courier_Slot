import argparse
import csv
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .forecast import BASELINE, forecast_cutoff, zone_forecasts
from .orders import HISTORY_DAYS, SOURCE, ZONES, load_history, write_daily_orders
from .results import FORECASTS, StorageUnavailable, latest_location, result_store
from .weather import DEMO_DATE, MODEL, weather_for_day


def run(
    target: date,
    data: Path,
    fetch_weather: bool,
    days: int = HISTORY_DAYS,
    zone_id: str | None = None,
) -> None:
    cutoff = forecast_cutoff(target)
    reference = target - timedelta(days=7)
    store = result_store(data)
    location = store.location(FORECASTS, target)
    daily_path = data / "processed" / f"orders_{target}_{days}d.csv"
    started = datetime.now(timezone.utc)
    run_id = f"{started:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}"
    journal = {
        "run_id": run_id,
        "started_at_utc": started.isoformat(),
        "target_date": target.isoformat(),
        "step": "orders",
    }
    try:
        history, new_days = load_history(target, data / "raw" / "orders", days)
        order_count = sum(sum(day.values()) for day in history.values())
        journal["orders"] = {
            "first_day": min(history).isoformat(),
            "last_day": max(history).isoformat(),
            "generated_days": new_days,
            "reused_days": len(history) - new_days,
            "orders": order_count,
            "new_day_source": "synthetic_event_log",
        }
        journal["step"] = "weather"
        weather, origin = weather_for_day(target, data / "raw" / "weather", fetch_weather)
        journal["weather"] = {"origin": origin, "model_run_utc": weather.model_run_utc}
        journal["step"] = "publish"
        forecasts = zone_forecasts(history[reference])
        result = {
            "target_date": target.isoformat(),
            "forecast_cutoff": cutoff.isoformat(),
            "orders_source": SOURCE,
            "model": BASELINE,
            "reference_date": reference.isoformat(),
            "weather": {"provider": "Open-Meteo", "model": MODEL, **asdict(weather)},
            "forecasts": forecasts,
        }
        write_daily_orders(daily_path, history)
        journal.update(
            status="success",
            step="done",
            forecast_file=location,
            daily_orders_file=daily_path.relative_to(data).as_posix(),
            finished_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        store.publish(FORECASTS, target, result, journal)
    except (OSError, StorageUnavailable, ValueError, csv.Error) as error:
        if journal.pop("forecast_file", None) is not None:
            journal["step"] = "publish"
        journal.update(
            status="failed",
            error=str(error),
            finished_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        try:
            previous = latest_location(store, (FORECASTS,))
            if previous is not None:
                journal["last_forecast_file"] = previous
            store.save_run(journal)
        except (OSError, StorageUnavailable) as log_error:
            print(f"Не удалось сохранить журнал прогона: {log_error}", file=sys.stderr)
        raise

    print(f"Прогноз на {target} · Москва · синтетические заказы")
    print(f"История: {len(history)} суток, {order_count} заказов. База: факт за {reference}.")
    print(f"Заказы: {new_days} новых суток, {len(history) - new_days} из сохранённых CSV.")
    print(
        f"Open-Meteo: {weather.mean_temperature_c:.2f} °C в среднем, "
        f"{weather.precipitation_mm:.2f} мм осадков."
    )
    print()
    print(f"{'Зона':<14} {'Прогноз':>8} {'Мощность':>9} {'Сверх плана':>12}")
    shown = [zone for zone in forecasts if zone_id is None or zone["zone_id"] == zone_id]
    for zone in shown:
        print(
            f"{zone['zone_name']:<14} {zone['forecast_orders']:>8} "
            f"{zone['capacity_orders']:>9} {zone['orders_over_capacity']:>12}"
        )
    print(f"\nРезультат: {location}")
    print(f"Запуск: {run_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Демо прогноза спроса по зонам доставки")
    parser.add_argument("--date", type=date.fromisoformat, default=DEMO_DATE, help="дата прогноза, ГГГГ-ММ-ДД")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="каталог данных")
    parser.add_argument(
        "--history-days", type=int, default=HISTORY_DAYS, help="объём истории заказов, от 14 до 366 суток"
    )
    parser.add_argument("--fetch-weather", action="store_true", help="загрузить выпуск Open-Meteo заново")
    parser.add_argument("--zone", choices=[zone.id for zone in ZONES], help="показать выбранную зону")
    args = parser.parse_args()
    if args.date < date(2024, 3, 15):
        parser.error("архив для выбранной модели доступен с прогноза на 2024-03-15")
    if not HISTORY_DAYS <= args.history_days <= 366:
        parser.error("--history-days должен быть от 14 до 366")

    try:
        run(args.date, args.data_dir, args.fetch_weather, args.history_days, args.zone)
    except (OSError, StorageUnavailable, ValueError, csv.Error) as error:
        print(f"Прогон прерван. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
