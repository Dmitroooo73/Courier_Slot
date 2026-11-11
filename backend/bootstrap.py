from os import getenv
from pathlib import Path

from .pipeline.__main__ import run
from .pipeline.results import PostgresResults, result_store
from .pipeline.weather import DEMO_DATE
from .services.auth import AuthStore


def main() -> None:
    data = Path(getenv("PULT_DATA_DIR", "data"))
    store = result_store(data)
    if not isinstance(store, PostgresResults):
        raise SystemExit("Для импорта задайте PULT_DATABASE_URL.")

    store.initialize()
    AuthStore(store).initialize()
    reports, runs = store.import_files(data)
    print(f"Обновлено в PostgreSQL — отчёты {reports}, записи журнала {runs}.", flush=True)
    if not store.dates():
        run(DEMO_DATE, data, fetch_weather=False)


if __name__ == "__main__":
    main()
