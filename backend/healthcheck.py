"""Container healthcheck for the worker: a heartbeat younger than ten minutes in the shared storage."""
import sys
from datetime import datetime, timedelta, timezone
from os import getenv
from pathlib import Path

from backend.pipeline.results import StorageUnavailable, result_store


def main() -> int:
    try:
        beat = result_store(Path(getenv("PULT_DATA_DIR", "data"))).heartbeat()
    except (OSError, StorageUnavailable):
        return 1
    if not beat:
        return 1
    age = datetime.now(timezone.utc) - datetime.fromisoformat(beat["at_utc"])
    return 0 if age < timedelta(minutes=10) else 1


if __name__ == "__main__":
    sys.exit(main())
