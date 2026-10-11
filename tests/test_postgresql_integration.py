from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

URL = os.getenv("PCB_QMS_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="PCB_QMS_TEST_DATABASE_URL is not configured")


def test_postgresql_concurrent_writes_and_rollback():
    engine = create_engine(URL, pool_size=8, max_overflow=8)
    table = f"concurrency_{uuid4().hex[:12]}"
    with engine.begin() as connection:
        connection.execute(text(f"CREATE TABLE {table}(id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, value INTEGER UNIQUE NOT NULL)"))
    try:
        def insert(value: int) -> None:
            with engine.begin() as connection:
                connection.execute(text(f"INSERT INTO {table}(value) VALUES (:value)"), {"value": value})

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(insert, range(64)))
        with engine.connect() as connection:
            assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 64
        with pytest.raises(RuntimeError), engine.begin() as connection:
            connection.execute(text(f"INSERT INTO {table}(value) VALUES (999)"))
            raise RuntimeError("rollback")
        with engine.connect() as connection:
            assert connection.execute(text(f"SELECT count(*) FROM {table} WHERE value=999")).scalar_one() == 0
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
        engine.dispose()
