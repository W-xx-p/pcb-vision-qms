from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.config import DATA_DIR, DATABASE_URL
from app.database import IS_SQLITE, create_database_dump
from app.services.infrastructure import mirror_file


def verify_restore(path: Path) -> dict:
    if IS_SQLITE:
        return {"verified": True, "mode": "sqlite"}
    url = make_url(DATABASE_URL).set(drivername="postgresql")
    verify_name = f"pcb_qms_verify_{uuid4().hex[:10]}"
    admin_url = url.set(database="postgres").render_as_string(hide_password=False)
    verify_url = url.set(database=verify_name).render_as_string(hide_password=False)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(verify_name)))
    try:
        # A newer client can emit SET transaction_timeout, which PostgreSQL 16
        # does not understand. Stream the archive as SQL and remove only that
        # forward-version session setting; ON_ERROR_STOP keeps verification strict.
        restored = subprocess.Popen(
            ["pg_restore", "--no-owner", "--file=-", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        loader = subprocess.Popen(
            ["psql", "--dbname", verify_url, "--set", "ON_ERROR_STOP=1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert restored.stdout is not None and restored.stderr is not None
        assert loader.stdin is not None
        for line in restored.stdout:
            if line.startswith("SET transaction_timeout"):
                continue
            loader.stdin.write(line)
        loader.stdin.close()
        loader.stdin = None
        _, loader_error = loader.communicate()
        restore_error = restored.stderr.read()
        restore_code = restored.wait()
        if restore_code or loader.returncode:
            raise RuntimeError(
                f"恢复验证执行失败: pg_restore={restore_code} psql={loader.returncode} "
                f"{restore_error} {loader_error}"
            )
        with psycopg.connect(verify_url) as connection:
            tables = connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]
            products = connection.execute("SELECT count(*) FROM products").fetchone()[0]
        return {
            "verified": tables >= 10,
            "tables": tables,
            "products": products,
            "restore_warning": "",
        }
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(verify_name)))


def run_backup(retention_days: int) -> dict:
    folder = DATA_DIR / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"quality-{stamp}.{'db' if IS_SQLITE else 'dump'}"
    create_database_dump(path)
    verification = verify_restore(path)
    if not verification.get("verified"):
        raise RuntimeError("备份恢复验证失败")
    object_key = mirror_file(path)
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    for old in folder.glob("quality-*"):
        if old != path and datetime.fromtimestamp(old.stat().st_mtime, UTC) < cutoff:
            old.unlink(missing_ok=True)
    return {"path": str(path), "bytes": path.stat().st_size, "object_key": object_key, **verification}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retention-days", type=int, default=14)
    args = parser.parse_args()
    print(json.dumps(run_backup(args.retention_days), ensure_ascii=False))


if __name__ == "__main__":
    main()
