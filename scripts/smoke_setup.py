"""Create a small SQLite database for smoke testing PR 11 CLI commands.

Usage:
    python scripts/smoke_setup.py

Reads DATAPRISM_DSN from environment. Drops and recreates two tables.
"""

import os

from sqlalchemy import create_engine, text


def main() -> None:
    dsn = os.environ.get("DATAPRISM_DSN")
    if not dsn:
        raise SystemExit("DATAPRISM_DSN must be set")

    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS users"))
            conn.execute(text("DROP TABLE IF EXISTS orders"))
            conn.execute(text("CREATE TABLE users (id INTEGER, email TEXT, name TEXT)"))
            conn.execute(text("INSERT INTO users VALUES (1, 'alice@example.com', 'Alice')"))
            conn.execute(text("INSERT INTO users VALUES (2, 'bob@example.com', 'Bob')"))
            conn.execute(text("CREATE TABLE orders (id INTEGER, customer_id INTEGER, total REAL)"))
            conn.execute(text("INSERT INTO orders VALUES (1, 1, 50.0)"))
            conn.execute(text("INSERT INTO orders VALUES (2, 2, 75.0)"))
    finally:
        engine.dispose()

    print(f"Smoke DB ready at: {dsn}")


if __name__ == "__main__":
    main()
