"""Package the estimator that produced the forecast and its training record."""
import pickle
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
from platform import python_version
from uuid import uuid4

from backend.pipeline.forecast import BASELINE
from backend.pipeline.results import ModelVersion

from .features import FEATURE_SCHEMA_VERSION


def runtime_versions() -> dict[str, str]:
    from .report import model_versions

    return {**model_versions(), "python": python_version(), "joblib": version("joblib")}


def make_model_version(report: dict, estimator, columns: list[str], *, published: bool) -> ModelVersion:
    created = datetime.now(timezone.utc)
    selected = report["selected"]
    model = selected["model"]
    artifact = pickle.dumps(estimator, protocol=5) if estimator is not None else b'{"lag_days":7}'
    validation = next(row for row in report["validation"]
                      if row["model"] == model and row["uses_weather"] == selected["uses_weather"])
    holdout = report["holdout"]
    metadata = {
        "version_id": f"{created:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:12]}",
        "created_at_utc": created.isoformat(),
        "target_date": report["target_date"],
        "status": "published" if published else "registered",
        **selected,
        "training": report["training"],
        "dataset_sha256": report["dataset_sha256"],
        "orders_source": report["orders_source"],
        "features": columns,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "parameters": report["parameters"][model],
        "seed": report["seed"],
        "versions": runtime_versions(),
        "validation": {key: validation[key] for key in ("mae_orders", "wape_percent", "observations", "windows")},
        "holdout": {key: holdout[key] for key in ("start", "end", "train_through", "selected", "baseline")},
        "artifact": {
            "format": "seasonal-rule-v1" if model == BASELINE else "pickle-v1",
            "sha256": sha256(artifact).hexdigest(),
            "bytes": len(artifact),
        },
    }
    return ModelVersion(metadata, artifact)


def load_estimator(saved: ModelVersion):
    """Read an internal artifact produced by the worker, never an uploaded file."""
    metadata = saved.metadata
    artifact = metadata["artifact"]
    if sha256(saved.artifact).hexdigest() != artifact["sha256"]:
        raise ValueError("Контрольная сумма сохранённой модели не совпала.")
    if metadata["feature_schema_version"] != FEATURE_SCHEMA_VERSION:
        raise ValueError("Сохранённая модель использует другую версию признаков.")
    if metadata["model"] == BASELINE and artifact["format"] == "seasonal-rule-v1":
        return None
    if artifact["format"] != "pickle-v1" or metadata["versions"] != runtime_versions():
        raise ValueError("Сохранённая модель несовместима с текущим окружением.")
    return pickle.loads(saved.artifact)
