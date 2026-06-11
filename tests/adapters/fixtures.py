"""Test fixtures for the database adapter test suite.

These are plain helper functions, not pytest fixtures. Tests call them
with a path (typically `tmp_path / "test.db"` from pytest's tmp_path
fixture) to create a SQLite database with a known schema.

Three database shapes:
- `make_minimal_db`: one table, one column, three rows. For basic
  connectivity tests.
- `make_users_db`: one table with diverse types (text, integer, NULL).
  For sampling and type-handling tests.
- `make_multi_table_db`: three tables with foreign keys. For schema
  introspection tests.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text


def make_minimal_db(path: Path) -> str:
    """Create a minimal SQLite database for connectivity tests.

    Schema:
        widgets (id INTEGER, name TEXT)
        - 3 rows

    Returns the DSN string (sqlite:///<path>).
    """
    dsn = f"sqlite:///{path}"
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE widgets (id INTEGER, name TEXT)"))
            conn.execute(text("INSERT INTO widgets VALUES (1, 'first')"))
            conn.execute(text("INSERT INTO widgets VALUES (2, 'second')"))
            conn.execute(text("INSERT INTO widgets VALUES (3, 'third')"))
    finally:
        engine.dispose()
    return dsn


def make_users_db(path: Path) -> str:
    """Create a SQLite database with diverse data types and NULLs.

    Schema:
        users (
            id INTEGER,
            email TEXT,
            name TEXT,           -- has a NULL row
            active INTEGER,      -- 1/0 boolean-style
            score REAL           -- floating point
        )
        - 5 rows, one with NULL name and NULL score

    Returns the DSN string.
    """
    dsn = f"sqlite:///{path}"
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE users ("
                    "id INTEGER, email TEXT, name TEXT, "
                    "active INTEGER, score REAL)"
                )
            )
            conn.execute(
                text("INSERT INTO users VALUES (1, 'alice@example.com', 'Alice', 1, 99.5)")
            )
            conn.execute(text("INSERT INTO users VALUES (2, 'bob@example.com', NULL, 0, NULL)"))
            conn.execute(
                text("INSERT INTO users VALUES (3, 'charlie@example.com', 'Charlie', 1, 87.2)")
            )
            conn.execute(
                text("INSERT INTO users VALUES (4, 'diana@example.com', 'Diana', 1, 92.0)")
            )
            conn.execute(text("INSERT INTO users VALUES (5, 'eve@example.com', 'Eve', 0, 50.5)"))
    finally:
        engine.dispose()
    return dsn


def make_multi_table_db(path: Path) -> str:
    """Create a SQLite database with multiple related tables.

    Schema:
        customers (id INTEGER, name TEXT, email TEXT)
        orders (id INTEGER, customer_id INTEGER, total REAL)
        products (id INTEGER, name TEXT, price REAL)

    Returns the DSN string.
    """
    dsn = f"sqlite:///{path}"
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE customers (id INTEGER, name TEXT, email TEXT)"))
            conn.execute(text("CREATE TABLE orders (id INTEGER, customer_id INTEGER, total REAL)"))
            conn.execute(text("CREATE TABLE products (id INTEGER, name TEXT, price REAL)"))
            conn.execute(text("INSERT INTO customers VALUES (1, 'Acme Corp', 'contact@acme.com')"))
            conn.execute(text("INSERT INTO orders VALUES (1, 1, 1500.00)"))
            conn.execute(text("INSERT INTO products VALUES (1, 'Widget', 99.99)"))
    finally:
        engine.dispose()
    return dsn


def make_postgres_test_table(
    dsn: str,
    *,
    with_data: bool = True,
) -> str:
    """Create a uniquely-named test table in the Postgres database.

    Returns the table name. The caller is responsible for dropping
    the table after the test (see drop_postgres_test_table).

    For pytest tests, use this with a yield-fixture for automatic
    cleanup - see test_postgres.py's test_table fixture.

    Schema:
        <table_name> (
            id INTEGER,
            email TEXT,
            name TEXT,           -- one NULL row
            active BOOLEAN,      -- real BOOLEAN type (Postgres-specific)
            score REAL           -- one NULL row
        )
        - 5 rows total when with_data=True, otherwise 0 rows

    Args:
        dsn: PostgreSQL connection string (postgresql+psycopg://...)
        with_data: If True (default), inserts 5 sample rows. If False,
            creates an empty table.

    Returns:
        The randomly-generated table name (e.g., 'test_users_a1b2c3d4').
    """
    table_name = f"test_users_{uuid4().hex[:8]}"
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""CREATE TABLE {table_name} (
                        id INTEGER,
                        email TEXT,
                        name TEXT,
                        active BOOLEAN,
                        score REAL
                    )"""
                )
            )
            if with_data:
                conn.execute(
                    text(
                        f"""INSERT INTO {table_name} VALUES
                        (1, 'alice@example.com', 'Alice', true, 99.5),
                        (2, 'bob@example.com', NULL, false, NULL),
                        (3, 'charlie@example.com', 'Charlie', true, 87.2),
                        (4, 'diana@example.com', 'Diana', true, 92.0),
                        (5, 'eve@example.com', 'Eve', false, 50.5)"""
                    )
                )
    finally:
        engine.dispose()
    return table_name


def drop_postgres_test_table(dsn: str, table_name: str) -> None:
    """Drop a test table. Safe to call on a non-existent table."""
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
    finally:
        engine.dispose()
