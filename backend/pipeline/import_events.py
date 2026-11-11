"""Import a completed service log without replacing an existing daily export."""
import argparse
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from .orders import import_event_log
from .storage import write_text


def main() -> None:
    parser = argparse.ArgumentParser(description="События заказов → проверенный суточный CSV")
    parser.add_argument("source", type=Path)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    destination = args.data_dir / "raw" / "orders" / f"{args.date}.csv"
    with TemporaryDirectory() as temporary:
        normalized = Path(temporary) / "orders.csv"
        stats = import_event_log(args.date, args.source, normalized)
        content = normalized.read_text(encoding="utf-8")
        if destination.exists() and destination.read_text(encoding="utf-8") != content:
            parser.error("На эту дату уже есть другие заказы. Исходный CSV сохранён без изменений.")
        if not destination.exists():
            write_text(destination, content)
    print(f"{destination}: {stats['events']} заказов; повторов событий: {stats['duplicate_events']}; служебных строк: {stats['ignored_lines']}")


if __name__ == "__main__":
    main()
