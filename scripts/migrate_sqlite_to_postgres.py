from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, func, select, text

from app.config import DATABASE_PATH
from app.database import IS_SQLITE, engine
from app.models import metadata

TABLE_ORDER = [
    "products",
    "production_lines",
    "stations",
    "reference_images",
    "model_versions",
    "training_jobs",
    "inspections",
    "quality_rules",
    "quality_alerts",
    "integration_events",
    "retraining_samples",
    "audit_logs",
    "schema_migrations",
]


def _convert(column: Any, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, Boolean):
        return bool(value)
    if isinstance(column.type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value)
    if isinstance(column.type, JSON) and isinstance(value, str):
        return json.loads(value or "{}")
    return value


def migrate(source_path: Path) -> dict[str, int]:
    if IS_SQLITE:
        raise RuntimeError("目标连接仍是SQLite，请设置PCB_QMS_DATABASE_URL为PostgreSQL")
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite源文件不存在: {source_path}")

    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    try:
        with engine.begin() as target:
            existing = sum(
                int(target.execute(select(func.count()).select_from(metadata.tables[name])).scalar_one())
                for name in TABLE_ORDER
            )
            if existing:
                raise RuntimeError(f"PostgreSQL业务表不是空库，已有{existing}条记录，已拒绝重复迁移")

            source_tables = {
                row[0]
                for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            for name in TABLE_ORDER:
                if name not in source_tables:
                    counts[name] = 0
                    continue
                table = metadata.tables[name]
                rows = source.execute(f'SELECT * FROM "{name}"').fetchall()
                payload = []
                for row in rows:
                    keys = set(row.keys())
                    payload.append(
                        {
                            column.name: _convert(column, row[column.name])
                            for column in table.columns
                            if column.name in keys
                        }
                    )
                if payload:
                    target.execute(table.insert(), payload)
                counts[name] = len(payload)

            for name in TABLE_ORDER:
                table = metadata.tables[name]
                if "id" not in table.c:
                    continue
                target.execute(
                    text(
                        "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                        "COALESCE((SELECT MAX(id) FROM " + name + "), 1), "
                        "EXISTS(SELECT 1 FROM " + name + "))"
                    ),
                    {"table_name": name},
                )
    finally:
        source.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate PCB QMS data from SQLite to PostgreSQL")
    parser.add_argument("--source", type=Path, default=DATABASE_PATH)
    args = parser.parse_args()
    counts = migrate(args.source.resolve())
    print(json.dumps({"status": "completed", "tables": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
