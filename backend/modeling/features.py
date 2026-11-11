from datetime import date, timedelta
from statistics import fmean

from backend.pipeline.orders import ZONES


CATEGORIES = ("zone_id", "weekday")
LAGS = (2, 7, 14)
ORDER_FEATURES = ("orders_lag_2", "orders_lag_7", "orders_lag_14", "mean_closed_week")


def features_for_day(
    target: date,
    history: dict[date, dict[str, int]],
    weather: dict[str, float | None],
) -> list[dict]:
    lag_days = [target - timedelta(days=lag) for lag in LAGS]
    closed_week = [target - timedelta(days=lag) for lag in range(2, 9)]
    rows = []
    for zone in ZONES:
        rows.append(
            {
                "date": target,
                "zone_id": zone.id,
                "weekday": str(target.weekday()),
                **{
                    f"orders_lag_{lag}": history[day][zone.id]
                    for lag, day in zip(LAGS, lag_days, strict=True)
                },
                "mean_closed_week": fmean(history[day][zone.id] for day in closed_week),
                **weather,
            }
        )
    return rows
