from collections.abc import Callable
from math import ceil, floor

import pandas as pd


SCENARIO_FEATURES = ("mean_temperature_c", "orders_lag_7")
MAX_INTERVALS = 40


def scenario_values(history: pd.Series, original: float | None) -> list[float]:
    observed = history.dropna()
    values = set()
    if not observed.empty:
        low, high = floor(observed.min()), ceil(observed.max())
        step = max(1, ceil((high - low) / MAX_INTERVALS))
        values.update(range(low, high + 1, step))
        values.add(high)
    if original is not None:
        values.add(original)
    return sorted(values)


def scenario_curves(
    train: pd.DataFrame,
    future: pd.DataFrame,
    counts: list[int],
    active_columns: list[str],
    predict_rows: Callable[[pd.DataFrame], list[int]],
) -> dict[str, list[dict]]:
    curves = {}
    for (_, inputs), base_orders in zip(future.iterrows(), counts, strict=True):
        zone = inputs["zone_id"]
        history = train[train["zone_id"] == zone]
        zone_curves = []
        for feature in SCENARIO_FEATURES:
            base_value = float(inputs[feature]) if pd.notna(inputs[feature]) else None
            values = scenario_values(history[feature], base_value)
            if not values:
                continue
            active = feature in active_columns
            if active:
                batch = pd.DataFrame([inputs] * len(values)).assign(**{feature: values})
                predictions = predict_rows(batch)
            else:
                predictions = [base_orders] * len(values)
            zone_curves.append({
                "feature": feature,
                "used": active,
                "base_value": base_value,
                "base_orders": base_orders,
                "points": [
                    {"value": value, "orders": predicted}
                    for value, predicted in zip(values, predictions, strict=True)
                ],
            })
        curves[zone] = zone_curves
    return curves
