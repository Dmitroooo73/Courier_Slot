"""Rebuild one saved report with fresh event logs and cached public weather."""
import argparse
from datetime import date
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory

from backend.pipeline.results import EVALUATIONS, result_store
from .report import build_report, dataset_digest, report_inputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка полного прогона на заново созданных заказах")
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    saved = result_store(args.data_dir).read(EVALUATIONS, args.date)
    with TemporaryDirectory(prefix="pult-reproduction-") as temporary:
        data = Path(temporary)
        copytree(args.data_dir / "raw" / "weather", data / "raw" / "weather")
        journal = {}
        rebuilt, _ = build_report(args.date, data, journal)
        for key in ("dataset_sha256", "selected", "forecasts", "validation", "holdout", "scenarios", "parameters", "versions"):
            if rebuilt[key] != saved[key]:
                raise ValueError(f"Воспроизведение не совпало: {key}")
        logs = list((data / "raw" / "order_events").glob("*.log"))
        if len(logs) != 365:
            raise ValueError("Создана неполная история событий")
        repeated = {}
        frame, _, _ = report_inputs(args.date, data, repeated)
        if repeated["orders"]["generated_days"] != 0 or dataset_digest(frame) != saved["dataset_sha256"]:
            raise ValueError("Повторная загрузка не идемпотентна")
        print("OK: 365 журналов событий → CSV → признаки → модели → прежние прогнозы и метрики.")
        print("OK: повторный запуск использует кэш, версия данных не меняется.")


if __name__ == "__main__":
    main()
