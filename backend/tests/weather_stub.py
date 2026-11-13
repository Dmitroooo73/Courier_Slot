"""Deterministic observed weather for tests: no network, varied enough to move synthetic demand."""
from datetime import timedelta


def observed_stub(first, last, _raw):
    days = {}
    day = first
    while day <= last:
        number = day.toordinal()
        days[day] = {"mean_temperature_c": float(number % 30 - 10), "precipitation_mm": float(number % 5),
                     "hours_with_precipitation": number % 7}
        day += timedelta(days=1)
    return days
