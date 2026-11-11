from datetime import date, datetime, time, timedelta

from .orders import MOSCOW, ZONES


BASELINE = "seasonal_naive_7d"


def forecast_cutoff(target: date) -> datetime:
    return datetime.combine(target - timedelta(days=1), time(20), tzinfo=MOSCOW)


def seasonal_explanation(count: int, reference_date: str) -> dict:
    return {
        "method": BASELINE,
        "base_orders": count,
        "raw_orders": count,
        "reference_date": reference_date,
        "contributions": [],
    }


def zone_forecasts(counts: dict[str, int]) -> list[dict]:
    forecasts = []
    for zone in ZONES:
        count = counts[zone.id]
        forecasts.append(
            {
                "zone_id": zone.id,
                "zone_name": zone.name,
                "forecast_orders": count,
                "capacity_orders": zone.capacity,
                "orders_over_capacity": max(0, count - zone.capacity),
            }
        )
    forecasts.sort(key=lambda zone: zone["forecast_orders"] - zone["capacity_orders"], reverse=True)
    return forecasts
