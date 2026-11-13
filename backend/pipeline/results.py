import json
from base64 import b64decode, b64encode
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from os import getenv
from pathlib import Path

from .storage import write_json


FORECASTS = "forecasts"
EVALUATIONS = "evaluations"
REPORT_KINDS = (FORECASTS, EVALUATIONS)
OPEN_CANDIDATE = ("queued", "training")
CANDIDATE_STATUSES = (*OPEN_CANDIDATE, "activated", "rejected", "failed")


def active_pointer(record: dict) -> dict:
    """The working-version row: the context a daily forecast needs, plus where it came from."""
    return {**record["context"], "version_id": record["version_id"],
            "activation_id": record["activation_id"], "activated_at_utc": record["at_utc"]}


def without_context(record: dict) -> dict:
    return {key: value for key, value in record.items() if key != "context"}


@dataclass(frozen=True)
class ModelVersion:
    metadata: dict
    artifact: bytes


class InvalidArtifact(ValueError):
    pass


class StorageUnavailable(RuntimeError):
    pass


def read_artifact(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as error:
        raise InvalidArtifact(path.name) from error
    if not isinstance(result, dict):
        raise InvalidArtifact(path.name)
    return result


class FileResults:
    def __init__(self, directory: Path):
        self.directory = directory

    def location(self, kind: str, target: date) -> str:
        return f"{kind}/{target}.json"

    def dates(self) -> list[date]:
        dates = set()
        for kind in REPORT_KINDS:
            for path in (self.directory / kind).glob("????-??-??.json"):
                try:
                    dates.add(date.fromisoformat(path.stem))
                except ValueError:
                    continue
        return sorted(dates, reverse=True)

    def has(self, kind: str, target: date) -> bool:
        return (self.directory / self.location(kind, target)).is_file()

    def read(self, kind: str, target: date) -> dict:
        return read_artifact(self.directory / self.location(kind, target))

    def recent_runs(self, limit: int) -> list[dict]:
        paths = sorted((self.directory / "runs").glob("*.json"), reverse=True)
        return [read_artifact(path) for path in paths[:limit]]

    def save_run(self, run: dict) -> None:
        write_json(self.directory / "runs" / (run["run_id"] + ".json"), run)

    def observation_path(self, target: date) -> Path:
        return self.directory / "observations" / f"{target}.json"

    def observations(self, limit: int | None = 30) -> list[dict]:
        paths = sorted((self.directory / "observations").glob("????-??-??.json"), reverse=True)
        return [read_artifact(path) for path in paths[:limit]]

    def pending_observations(self, today: date) -> list[dict]:
        pending = []
        for path in sorted((self.directory / "observations").glob("????-??-??.json")):
            if date.fromisoformat(path.stem) < today:
                record = read_artifact(path)
                if record["actuals"] is None:
                    pending.append(record)
        return pending

    def save_observation(self, target: date, record: dict) -> None:
        write_json(self.observation_path(target), record)

    def save_decisions(self, decisions: list[dict]) -> int:
        saved = 0
        for record in decisions:
            path = self.directory / "monitoring" / record["rule_version"] / record["zone_id"] / (record["end_date"] + ".json")
            if not path.exists():
                write_json(path, record)
                saved += 1
        return saved

    def decisions(self, rule_version: str, zone: str, limit: int = 30, before: date | None = None) -> list[dict]:
        paths = sorted((self.directory / "monitoring" / rule_version / zone).glob("????-??-??.json"), reverse=True)
        if before:
            paths = [path for path in paths if path.stem < before.isoformat()]
        return [read_artifact(path) for path in paths[:limit]]

    def latest_decisions(self, rule_version: str) -> list[dict]:
        root = self.directory / "monitoring" / rule_version
        return [record for zone in sorted(root.glob("*")) if zone.is_dir()
                for record in self.decisions(rule_version, zone.name, limit=1)]

    def drift_reference(self, rule_version: str) -> dict | None:
        try:
            return read_artifact(self.directory / "drift" / rule_version / "reference.json")
        except FileNotFoundError:
            return None

    def latest_drift(self, rule_version: str) -> dict | None:
        root = self.directory / "drift" / rule_version
        path = max(root.glob("????-??-??.json"), default=None)
        return read_artifact(path) if path is not None else None

    def save_drift(self, report: dict, reference: dict | None) -> None:
        root = self.directory / "drift" / report["rule_version"]
        reference_path = root / "reference.json"
        if reference is not None and not reference_path.exists():
            write_json(reference_path, reference)
        report_path = root / (report["end_date"] + ".json")
        if not report_path.exists():
            write_json(report_path, report)

    def save_model(self, model: ModelVersion) -> None:
        path = self.directory / "model_registry" / (model.metadata["version_id"] + ".json")
        if not path.exists():
            write_json(path, {"metadata": model.metadata, "artifact": b64encode(model.artifact).decode("ascii")})

    def model_version(self, version_id: str) -> ModelVersion:
        bundle = read_artifact(self.directory / "model_registry" / (version_id + ".json"))
        return ModelVersion(bundle["metadata"], b64decode(bundle["artifact"], validate=True))

    def model_versions(self, limit: int, before: str | None = None) -> list[dict]:
        paths = sorted((self.directory / "model_registry").glob("*.json"), reverse=True)
        page = [path for path in paths if before is None or path.stem < before][:limit]
        return [read_artifact(path)["metadata"] for path in page]

    def model_metadata(self, version_id: str) -> dict:
        return read_artifact(self.directory / "model_registry" / (version_id + ".json"))["metadata"]

    def active_model(self) -> dict | None:
        try:
            return read_artifact(self.directory / "active_forecast_model.json")
        except FileNotFoundError:
            return None

    def activate_model(self, record: dict, *, expected: str | None, model: ModelVersion | None = None,
                       candidate: dict | None = None) -> bool:
        """Switch the working version only if `expected` is still active (None: nothing active yet)."""
        current = self.active_model()
        if (current["version_id"] if current else None) != expected:
            return False
        if model is not None:
            self.save_model(model)
        self.model_metadata(record["version_id"])
        if candidate is not None:
            self.save_candidate(candidate)
        write_json(self.directory / "model_activations" / (record["activation_id"] + ".json"), record)
        write_json(self.directory / "active_forecast_model.json", active_pointer(record))
        return True

    def activations(self, limit: int = 20) -> list[dict]:
        paths = sorted((self.directory / "model_activations").glob("*.json"), reverse=True)
        return [without_context(read_artifact(path)) for path in paths[:limit]]

    def activation_context(self, version_id: str) -> dict | None:
        for path in sorted((self.directory / "model_activations").glob("*.json"), reverse=True):
            record = read_artifact(path)
            if record["version_id"] == version_id:
                return record["context"]
        return None

    def candidates(self, limit: int = 10) -> list[dict]:
        paths = sorted((self.directory / "model_candidates").glob("*.json"), reverse=True)
        return [read_artifact(path) for path in paths[:limit]]

    def open_candidate(self) -> dict | None:
        return next((job for job in self.candidates(limit=None) if job["status"] in OPEN_CANDIDATE), None)

    def queue_candidate(self, job: dict) -> tuple[dict, bool]:
        existing = next((item for item in self.candidates(limit=None) if item["trigger_key"] == job["trigger_key"]), None)
        if existing is not None:
            return existing, False
        if (current := self.open_candidate()) is not None:
            return current, False
        self.save_candidate(job)
        return job, True

    def save_candidate(self, job: dict, model: ModelVersion | None = None) -> None:
        if model is not None:
            self.save_model(model)
        write_json(self.directory / "model_candidates" / (job["candidate_id"] + ".json"), job)

    def lifecycle_demo(self, demo_version: str) -> dict | None:
        try:
            return read_artifact(self.directory / "lifecycle_demo" / (demo_version + ".json"))
        except FileNotFoundError:
            return None

    def save_lifecycle_demo(self, summary: dict) -> None:
        write_json(self.directory / "lifecycle_demo" / (summary["demo_version"] + ".json"), summary)

    def publish(
        self, kind: str, target: date, report: dict, run: dict | None = None,
        observation: dict | None = None, *, model: ModelVersion | None = None,
    ) -> None:
        if model is not None:
            self.save_model(model)
        write_json(self.directory / self.location(kind, target), report)
        if observation is not None and not self.observation_path(target).is_file():
            self.save_observation(target, observation)
        if run is not None:
            self.save_run(run)


class PostgresResults:
    def __init__(self, connection_info: str):
        self.connection_info = connection_info

    @contextmanager
    def connection(self, *, autocommit: bool = False):
        import psycopg

        try:
            with psycopg.connect(
                self.connection_info, connect_timeout=5, autocommit=autocommit
            ) as connection:
                yield connection
        except psycopg.Error as error:
            raise StorageUnavailable("Не удалось выполнить операцию с PostgreSQL.") from error

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS forecast_reports (
                    kind text NOT NULL CHECK (kind IN ('forecasts', 'evaluations')),
                    target_date date NOT NULL,
                    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
                    PRIMARY KEY (kind, target_date)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id text PRIMARY KEY,
                    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object')
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS forecast_observations (
                    target_date date PRIMARY KEY,
                    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object')
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS monitoring_decisions (
                    rule_version text NOT NULL,
                    zone_id text NOT NULL,
                    end_date date NOT NULL,
                    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
                    PRIMARY KEY (rule_version, zone_id, end_date)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS drift_references (
                    rule_version text PRIMARY KEY,
                    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object')
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS drift_reports (
                    rule_version text NOT NULL,
                    end_date date NOT NULL,
                    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
                    PRIMARY KEY (rule_version, end_date)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS model_versions (
                    version_id text PRIMARY KEY,
                    metadata jsonb NOT NULL CHECK (jsonb_typeof(metadata) = 'object'),
                    artifact bytea NOT NULL CHECK (octet_length(artifact) > 0)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS active_forecast_model (
                    singleton boolean PRIMARY KEY CHECK (singleton),
                    version_id text NOT NULL REFERENCES model_versions(version_id),
                    context jsonb NOT NULL CHECK (jsonb_typeof(context) = 'object')
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS model_activations (
                    activation_id text PRIMARY KEY,
                    version_id text NOT NULL REFERENCES model_versions(version_id),
                    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object')
                )
            """)
            connection.execute(f"""
                CREATE TABLE IF NOT EXISTS model_candidates (
                    candidate_id text PRIMARY KEY,
                    trigger_key text NOT NULL UNIQUE,
                    status text NOT NULL CHECK (status IN ({", ".join(f"'{status}'" for status in CANDIDATE_STATUSES)})),
                    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object')
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS lifecycle_demos (
                    demo_version text PRIMARY KEY,
                    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object')
                )
            """)
            # At most one queued or training candidate, even if the web and worker race.
            connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS model_candidates_single_open
                ON model_candidates ((status IN ('queued', 'training'))) WHERE status IN ('queued', 'training')
            """)

    def location(self, kind: str, target: date) -> str:
        return f"postgresql:forecast_reports/{kind}/{target}"

    def dates(self) -> list[date]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT target_date FROM forecast_reports ORDER BY target_date DESC"
            ).fetchall()
        return [row[0] for row in rows]

    def has(self, kind: str, target: date) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM forecast_reports WHERE kind = %s AND target_date = %s", (kind, target)
            ).fetchone()
        return row is not None

    def read(self, kind: str, target: date) -> dict:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM forecast_reports WHERE kind = %s AND target_date = %s", (kind, target)
            ).fetchone()
        if row is None:
            raise FileNotFoundError(self.location(kind, target))
        return row[0]

    def recent_runs(self, limit: int) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM pipeline_runs ORDER BY run_id DESC LIMIT %s", (limit,)
            ).fetchall()
        return [row[0] for row in rows]

    def save_run(self, run: dict) -> None:
        from psycopg.types.json import Jsonb

        with self.connection() as connection:
            connection.execute(
                "INSERT INTO pipeline_runs (run_id, payload) VALUES (%s, %s)",
                (run["run_id"], Jsonb(run)),
            )

    def observations(self, limit: int | None = 30) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM forecast_observations ORDER BY target_date DESC LIMIT %s", (limit,)
            ).fetchall()
        return [row[0] for row in rows]

    def pending_observations(self, today: date) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute("""
                SELECT payload FROM forecast_observations
                WHERE target_date < %s AND payload->'actuals' = 'null'::jsonb
                ORDER BY target_date
            """, (today,)).fetchall()
        return [row[0] for row in rows]

    def save_observation(self, target: date, record: dict) -> None:
        from psycopg.types.json import Jsonb

        with self.connection() as connection:
            connection.execute("""
                UPDATE forecast_observations SET payload = %s
                WHERE target_date = %s AND payload->'actuals' = 'null'::jsonb
            """, (Jsonb(record), target))

    def save_decisions(self, decisions: list[dict]) -> int:
        from psycopg.types.json import Jsonb

        saved = 0
        with self.connection() as connection:
            for record in decisions:
                result = connection.execute("""
                    INSERT INTO monitoring_decisions (rule_version, zone_id, end_date, payload)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (rule_version, zone_id, end_date) DO NOTHING
                """, (record["rule_version"], record["zone_id"], record["end_date"], Jsonb(record)))
                saved += result.rowcount
        return saved

    def decisions(self, rule_version: str, zone: str, limit: int = 30, before: date | None = None) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute("""
                SELECT payload FROM monitoring_decisions
                WHERE rule_version = %s AND zone_id = %s AND (%s::date IS NULL OR end_date < %s)
                ORDER BY end_date DESC LIMIT %s
            """, (rule_version, zone, before, before, limit)).fetchall()
        return [row[0] for row in rows]

    def latest_decisions(self, rule_version: str) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute("""
                SELECT DISTINCT ON (zone_id) payload FROM monitoring_decisions
                WHERE rule_version = %s ORDER BY zone_id, end_date DESC
            """, (rule_version,)).fetchall()
        return [row[0] for row in rows]

    def drift_reference(self, rule_version: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("SELECT payload FROM drift_references WHERE rule_version = %s",
                                     (rule_version,)).fetchone()
        return row[0] if row else None

    def latest_drift(self, rule_version: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("""
                SELECT payload FROM drift_reports WHERE rule_version = %s ORDER BY end_date DESC LIMIT 1
            """, (rule_version,)).fetchone()
        return row[0] if row else None

    def save_drift(self, report: dict, reference: dict | None) -> None:
        from psycopg.types.json import Jsonb

        with self.connection() as connection:
            if reference is not None:
                connection.execute("""
                    INSERT INTO drift_references (rule_version, payload) VALUES (%s, %s)
                    ON CONFLICT (rule_version) DO NOTHING
                """, (report["rule_version"], Jsonb(reference)))
            connection.execute("""
                INSERT INTO drift_reports (rule_version, end_date, payload) VALUES (%s, %s, %s)
                ON CONFLICT (rule_version, end_date) DO NOTHING
            """, (report["rule_version"], report["end_date"], Jsonb(report)))

    @staticmethod
    def insert_model(connection, model: ModelVersion) -> None:
        from psycopg.types.json import Jsonb

        connection.execute("""
            INSERT INTO model_versions (version_id, metadata, artifact) VALUES (%s, %s, %s)
            ON CONFLICT (version_id) DO NOTHING
        """, (model.metadata["version_id"], Jsonb(model.metadata), model.artifact))

    def save_model(self, model: ModelVersion) -> None:
        with self.connection() as connection:
            self.insert_model(connection, model)

    def model_version(self, version_id: str) -> ModelVersion:
        with self.connection() as connection:
            row = connection.execute("SELECT metadata, artifact FROM model_versions WHERE version_id = %s",
                                     (version_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(version_id)
        return ModelVersion(*row)

    def model_versions(self, limit: int, before: str | None = None) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute("""
                SELECT metadata FROM model_versions
                WHERE (%s::text IS NULL OR version_id < %s)
                ORDER BY version_id DESC LIMIT %s
            """, (before, before, limit)).fetchall()
        return [row[0] for row in rows]

    def model_metadata(self, version_id: str) -> dict:
        with self.connection() as connection:
            row = connection.execute("SELECT metadata FROM model_versions WHERE version_id = %s", (version_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(version_id)
        return row[0]

    def active_model(self) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("SELECT version_id, context FROM active_forecast_model WHERE singleton").fetchone()
        return {**row[1], "version_id": row[0]} if row else None

    @staticmethod
    def insert_activation(connection, record: dict) -> None:
        from psycopg.types.json import Jsonb

        connection.execute("""
            INSERT INTO model_activations (activation_id, version_id, payload) VALUES (%s, %s, %s)
            ON CONFLICT (activation_id) DO NOTHING
        """, (record["activation_id"], record["version_id"], Jsonb(record)))

    @staticmethod
    def write_candidate(connection, job: dict) -> None:
        from psycopg.types.json import Jsonb

        connection.execute("""
            UPDATE model_candidates SET status = %s, payload = %s WHERE candidate_id = %s
        """, (job["status"], Jsonb(job), job["candidate_id"]))

    def activate_model(self, record: dict, *, expected: str | None, model: ModelVersion | None = None,
                       candidate: dict | None = None) -> bool:
        """Switch the working version only if `expected` is still active (None: nothing active yet)."""
        from psycopg.types.json import Jsonb

        pointer = active_pointer(record)
        context = Jsonb({key: value for key, value in pointer.items() if key != "version_id"})
        with self.connection() as connection:
            # The pointer references model_versions, so a new version is stored first.
            if model is not None:
                self.insert_model(connection, model)
            if expected is None:
                switched = connection.execute("""
                    INSERT INTO active_forecast_model (singleton, version_id, context) VALUES (true, %s, %s)
                    ON CONFLICT (singleton) DO NOTHING
                """, (record["version_id"], context)).rowcount == 1
            else:
                switched = connection.execute("""
                    UPDATE active_forecast_model SET version_id = %s, context = %s
                    WHERE singleton AND version_id = %s
                """, (record["version_id"], context, expected)).rowcount == 1
            if not switched:
                connection.rollback()
                return False
            self.insert_activation(connection, record)
            if candidate is not None:
                self.write_candidate(connection, candidate)
        return True

    def activations(self, limit: int = 20) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute("""
                SELECT payload - 'context' FROM model_activations ORDER BY activation_id DESC LIMIT %s
            """, (limit,)).fetchall()
        return [row[0] for row in rows]

    def activation_context(self, version_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("""
                SELECT payload->'context' FROM model_activations WHERE version_id = %s
                ORDER BY activation_id DESC LIMIT 1
            """, (version_id,)).fetchone()
        return row[0] if row else None

    def candidates(self, limit: int | None = 10) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM model_candidates ORDER BY candidate_id DESC LIMIT %s", (limit,)
            ).fetchall()
        return [row[0] for row in rows]

    def open_candidate(self) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("""
                SELECT payload FROM model_candidates WHERE status IN ('queued', 'training')
                ORDER BY candidate_id LIMIT 1
            """).fetchone()
        return row[0] if row else None

    def queue_candidate(self, job: dict) -> tuple[dict, bool]:
        from psycopg import errors
        from psycopg.types.json import Jsonb

        try:
            with self.connection() as connection:
                existing = connection.execute("SELECT payload FROM model_candidates WHERE trigger_key = %s",
                                              (job["trigger_key"],)).fetchone()
                if existing:
                    return existing[0], False
                connection.execute("""
                    INSERT INTO model_candidates (candidate_id, trigger_key, status, payload) VALUES (%s, %s, %s, %s)
                """, (job["candidate_id"], job["trigger_key"], job["status"], Jsonb(job)))
            return job, True
        except StorageUnavailable as error:
            # A concurrent request inserted the same trigger or another open job first.
            if not isinstance(error.__cause__, (errors.UniqueViolation, errors.ExclusionViolation)):
                raise
        with self.connection() as connection:
            row = connection.execute("""
                SELECT payload FROM model_candidates WHERE trigger_key = %s OR status IN ('queued', 'training')
                ORDER BY (trigger_key = %s) DESC LIMIT 1
            """, (job["trigger_key"], job["trigger_key"])).fetchone()
        return row[0], False

    def save_candidate(self, job: dict, model: ModelVersion | None = None) -> None:
        with self.connection() as connection:
            if model is not None:
                self.insert_model(connection, model)
            self.write_candidate(connection, job)

    def lifecycle_demo(self, demo_version: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("SELECT payload FROM lifecycle_demos WHERE demo_version = %s",
                                     (demo_version,)).fetchone()
        return row[0] if row else None

    def save_lifecycle_demo(self, summary: dict) -> None:
        """A replay on isolated data; kept apart from working forecasts, decisions and versions."""
        from psycopg.types.json import Jsonb

        with self.connection() as connection:
            connection.execute("""
                INSERT INTO lifecycle_demos (demo_version, payload) VALUES (%s, %s)
                ON CONFLICT (demo_version) DO UPDATE SET payload = EXCLUDED.payload
            """, (summary["demo_version"], Jsonb(summary)))

    def publish(
        self, kind: str, target: date, report: dict, run: dict | None = None,
        observation: dict | None = None, *, model: ModelVersion | None = None,
    ) -> None:
        from psycopg.types.json import Jsonb

        with self.connection() as connection:
            if model is not None:
                self.insert_model(connection, model)
            connection.execute("""
                INSERT INTO forecast_reports (kind, target_date, payload) VALUES (%s, %s, %s)
                ON CONFLICT (kind, target_date) DO UPDATE SET payload = EXCLUDED.payload
            """, (kind, target, Jsonb(report)))
            if observation is not None:
                connection.execute("""
                    INSERT INTO forecast_observations (target_date, payload) VALUES (%s, %s)
                    ON CONFLICT (target_date) DO NOTHING
                """, (target, Jsonb(observation)))
            if run is not None:
                connection.execute(
                    "INSERT INTO pipeline_runs (run_id, payload) VALUES (%s, %s)",
                    (run["run_id"], Jsonb(run)),
                )

    @contextmanager
    def daily_lock(self):
        with self.connection(autocommit=True) as connection:
            acquired, = connection.execute("SELECT pg_try_advisory_lock(64317925)").fetchone()
            if not acquired:
                raise RuntimeError("В этой базе уже выполняется ежедневный прогон.")
            yield

    def import_files(self, directory: Path) -> tuple[int, int]:
        from psycopg.types.json import Jsonb

        source = FileResults(directory)
        reports = runs = 0
        with self.connection() as connection:
            for path in sorted((directory / "model_registry").glob("*.json")):
                self.insert_model(connection, source.model_version(path.stem))
            for path in sorted((directory / "model_activations").glob("*.json")):
                self.insert_activation(connection, read_artifact(path))
            for job in reversed(source.candidates(limit=None)):
                connection.execute("""
                    INSERT INTO model_candidates (candidate_id, trigger_key, status, payload) VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (job["candidate_id"], job["trigger_key"], job["status"], Jsonb(job)))
            if (active := source.active_model()) is not None:
                connection.execute("""
                    INSERT INTO active_forecast_model (singleton, version_id, context) VALUES (true, %s, %s)
                    ON CONFLICT (singleton) DO NOTHING
                """, (active["version_id"], Jsonb({key: value for key, value in active.items() if key != "version_id"})))
            for target in source.dates():
                for kind in REPORT_KINDS:
                    if not source.has(kind, target):
                        continue
                    result = connection.execute("""
                        INSERT INTO forecast_reports (kind, target_date, payload) VALUES (%s, %s, %s)
                        ON CONFLICT (kind, target_date) DO UPDATE SET payload = jsonb_set(
                            forecast_reports.payload, '{feature_importance}', EXCLUDED.payload->'feature_importance'
                        )
                        WHERE forecast_reports.kind = 'evaluations'
                          AND NOT (forecast_reports.payload ? 'feature_importance')
                          AND EXCLUDED.payload ? 'feature_importance'
                          AND forecast_reports.payload->>'dataset_sha256' = EXCLUDED.payload->>'dataset_sha256'
                          AND forecast_reports.payload->'selected' = EXCLUDED.payload->'selected'
                          AND forecast_reports.payload->'parameters' = EXCLUDED.payload->'parameters'
                          AND forecast_reports.payload->'versions' = EXCLUDED.payload->'versions'
                    """, (kind, target, Jsonb(source.read(kind, target))))
                    reports += result.rowcount
            for path in sorted((directory / "runs").glob("*.json")):
                run = read_artifact(path)
                result = connection.execute("""
                    INSERT INTO pipeline_runs (run_id, payload) VALUES (%s, %s)
                    ON CONFLICT (run_id) DO NOTHING
                """, (run["run_id"], Jsonb(run)))
                runs += result.rowcount
            for path in sorted((directory / "observations").glob("????-??-??.json")):
                connection.execute("""
                    INSERT INTO forecast_observations (target_date, payload) VALUES (%s, %s)
                    ON CONFLICT (target_date) DO NOTHING
                """, (date.fromisoformat(path.stem), Jsonb(read_artifact(path))))
        self.save_decisions([read_artifact(path) for path in sorted(
            (directory / "monitoring").glob("*/*/????-??-??.json"))])
        return reports, runs


def result_store(directory: Path) -> FileResults | PostgresResults:
    connection_info = getenv("PULT_DATABASE_URL")
    return PostgresResults(connection_info) if connection_info else FileResults(directory)


def latest_location(store: FileResults | PostgresResults, kinds: tuple[str, ...] = REPORT_KINDS) -> str | None:
    for target in store.dates():
        for kind in reversed(kinds):
            if store.has(kind, target):
                return store.location(kind, target)
    return None
