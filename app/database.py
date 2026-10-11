from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, create_engine, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, CursorResult, Engine, make_url

from app.config import DATABASE_PATH, DATABASE_URL, ensure_directories
from app.models import metadata

IS_SQLITE = DATABASE_URL.startswith("sqlite")
engine: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=not IS_SQLITE,
    pool_recycle=1800 if not IS_SQLITE else -1,
    connect_args={"timeout": 30, "check_same_thread": False} if IS_SQLITE else {},
)
DATABASE_BACKEND = engine.dialect.name


if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection: sqlite3.Connection, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.close()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _prepare_statement(query: str, params: Sequence[Any]) -> tuple[Any, dict[str, Any]]:
    parts = query.split("?")
    if len(parts) - 1 != len(params):
        raise ValueError("SQL参数数量与占位符不一致")
    sql = parts[0]
    values: dict[str, Any] = {}
    json_query = "_json" in query.lower()
    json_binds: list[str] = []
    for index, value in enumerate(params):
        name = f"p{index}"
        sql += f":{name}" + parts[index + 1]
        if not IS_SQLITE and isinstance(value, str):
            if json_query and value[:1] in {"{", "["}:
                try:
                    value = json.loads(value)
                    json_binds.append(name)
                except json.JSONDecodeError:
                    pass
            elif "T" in value:
                try:
                    value = datetime.fromisoformat(value)
                except ValueError:
                    pass
        values[name] = value
    statement = text(sql)
    for name in json_binds:
        statement = statement.bindparams(bindparam(name, type_=JSONB))
    return statement, values


class DatabaseConnection:
    def __init__(self, connection: Connection):
        self.connection = connection

    def execute(self, query: str, params: Sequence[Any] = ()) -> CursorResult[Any]:
        statement, values = _prepare_statement(query, params)
        return self.connection.execute(statement, values)

    def fetch_one(self, query: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        row = self.execute(query, params).fetchone()
        return _row_dict(row) if row else None

    def fetch_all(self, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return [_row_dict(row) for row in self.execute(query, params).fetchall()]


@contextmanager
def connect() -> Iterator[DatabaseConnection]:
    ensure_directories()
    with engine.begin() as connection:
        yield DatabaseConnection(connection)


def init_database() -> None:
    """Create tables for local SQLite tests; production schema is managed by Alembic."""
    ensure_directories()
    if IS_SQLITE:
        metadata.create_all(engine)
    if not fetch_one("SELECT version FROM schema_migrations WHERE version = ?", (1,)):
        with connect() as db:
            db.execute(
                "INSERT INTO schema_migrations(version, description, applied_at) VALUES (?, ?, ?)",
                (1, "initial enterprise schema", utc_now()),
            )


def _row_dict(row: Any) -> dict[str, Any]:
    result = dict(row._mapping)
    for key, value in tuple(result.items()):
        if key.endswith("_json") and isinstance(value, (dict, list)):
            result[key] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
    return result


def audit(
    action: str,
    entity_type: str,
    entity_id: int | str,
    details: dict[str, Any] | None = None,
    actor: str = "system",
    db: DatabaseConnection | None = None,
) -> None:
    def write(connection: DatabaseConnection) -> None:
        connection.execute(
            """
            INSERT INTO audit_logs(actor, action, entity_type, entity_id, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (actor, action, entity_type, str(entity_id), json.dumps(details or {}, ensure_ascii=False), utc_now()),
        )
    if db is not None:
        write(db)
    else:
        with connect() as connection:
            write(connection)


def enqueue_event(
    event_type: str,
    entity_id: int | str,
    payload: dict[str, Any],
    db: DatabaseConnection | None = None,
    idempotency_key: str | None = None,
) -> int:
    def write(connection: DatabaseConnection) -> int:
        cursor = connection.execute(
            """
            INSERT INTO integration_events(
                event_type, entity_id, payload_json, idempotency_key, created_at
            ) VALUES (?, ?, ?, ?, ?) RETURNING id
            """,
            (
                event_type,
                str(entity_id),
                json.dumps(payload, ensure_ascii=False),
                idempotency_key or f"{event_type}:{entity_id}:{uuid4().hex}",
                utc_now(),
            ),
        )
        return int(cursor.scalar_one())
    if db is not None:
        return write(db)
    with connect() as connection:
        return write(connection)


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as db:
        return [_row_dict(row) for row in db.execute(query, params).fetchall()]


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute(query, params).fetchone()
        return _row_dict(row) if row else None


def create_database_dump(destination: Path) -> None:
    if IS_SQLITE:
        source = sqlite3.connect(DATABASE_PATH, timeout=30)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return

    url = make_url(DATABASE_URL).set(drivername="postgresql")
    try:
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--file", str(destination), url.render_as_string(hide_password=False)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        destination.unlink(missing_ok=True)
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise RuntimeError(f"PostgreSQL备份失败: {detail}") from exc


def dispose_engine() -> None:
    engine.dispose()
