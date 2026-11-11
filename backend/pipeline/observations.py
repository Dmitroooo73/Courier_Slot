from datetime import date, datetime, timezone
from pathlib import Path

from .orders import MOSCOW, count_orders, generate_orders
from .results import FileResults, PostgresResults


def observation_record(target: date, report: dict, published: datetime) -> dict | None:
    if published.astimezone(MOSCOW).date() >= target:
        return None
    selected = report["selected"]
    validation = next(
        row for row in report["validation"]
        if row["model"] == selected["model"] and row["uses_weather"] == selected["uses_weather"]
    )
    return {
        "target_date": target.isoformat(),
        "published_at_utc": published.astimezone(timezone.utc).isoformat(),
        "observed_at_utc": None,
        "model": selected["model"],
        "uses_weather": selected["uses_weather"],
        "orders_source": report["orders_source"],
        "forecasts": {zone["zone_id"]: zone["forecast_orders"] for zone in report["forecasts"]},
        "validation_mae": {zone: score["mae_orders"] for zone, score in validation["by_zone"].items()},
        "actuals": None,
    }


def collect_closed_days(store: FileResults | PostgresResults, data: Path, now: datetime) -> int:
    pending = store.pending_observations(now.astimezone(MOSCOW).date())
    for record in pending:
        target = date.fromisoformat(record["target_date"])
        path = data / "raw" / "orders" / f"{target}.csv"
        if not path.is_file():
            if record["orders_source"] != "synthetic":
                raise FileNotFoundError(path)
            generate_orders(target, path)
        actuals = count_orders(target, path, set())
        store.save_observation(target, {
            **record,
            "actuals": actuals,
            "observed_at_utc": now.astimezone(timezone.utc).isoformat(),
        })
    return len(pending)
