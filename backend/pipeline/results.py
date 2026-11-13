import json
from contextlib import contextmanager
from datetime import date
from os import getenv
from pathlib import Path

from .storage import write_json


FORECASTS = "forecasts"
EVALUATIONS = "evaluations"
REPORT_KINDS = (FORECASTS, EVALUATIONS)


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

    def publish(
        self, kind: str, target: date, report: dict, run: dict | None = None, observation: dict | None = None
    ) -> None:
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

    def publish(
        self, kind: str, target: date, report: dict, run: dict | None = None, observation: dict | None = None
    ) -> None:
        from psycopg.types.json import Jsonb

        with self.connection() as connection:
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
