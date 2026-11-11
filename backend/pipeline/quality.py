"""Data checks independent of model fitting; no future values in anomaly limits."""
from datetime import date
from statistics import median

from .orders import ZONES


def quality_summary(history: dict[date, dict[str, int]], weather: dict) -> dict:
    previous: dict[tuple[str, int], list[int]] = {}
    anomalies = []
    for day, counts in sorted(history.items()):
        for zone in ZONES:
            value = counts[zone.id]
            window = previous.setdefault((zone.id, day.weekday()), [])
            recent = window[-8:]
            if len(recent) >= 4:
                usual = median(recent)
                mad = median(abs(item - usual) for item in recent)
                threshold = max(6 * mad, usual * 0.5, 1)
                if abs(value - usual) > threshold:
                    anomalies.append({"date": day.isoformat(), "zone_id": zone.id, "orders": value,
                                      "usual_orders": usual, "allowed_difference": threshold})
            window.append(value)
    return {
        "checked_days": len(history),
        "checked_orders": sum(sum(counts.values()) for counts in history.values()),
        "anomaly_count": len(anomalies),
        "anomaly_examples": anomalies[-10:],
        "missing_weather_days": sum(any(v is None for v in values.values()) for values in weather.values()),
        "policy": "invalid_records_block_publication; unusual_demand_is_flagged_and_kept",
    }
