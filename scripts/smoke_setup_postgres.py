"""Create test tables in the Docker Postgres for smoke testing PR 11.

Usage:
    $env:DATAPRISM_DSN = "postgresql://postgres:devpassword@localhost:5432/dataprism"
    python scripts/smoke_setup_postgres.py

Drops and recreates three tables in the 'public' schema.
"""

import os

from sqlalchemy import create_engine, text


def main() -> None:
    raw_dsn = os.environ.get("DATAPRISM_DSN")
    if not raw_dsn:
        raise SystemExit("DATAPRISM_DSN must be set")

    # Normalize for SQLAlchemy
    if raw_dsn.startswith("postgresql://"):
        dsn = raw_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    else:
        dsn = raw_dsn

    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
            conn.execute(text("DROP TABLE IF EXISTS orders CASCADE"))
            conn.execute(text("DROP TABLE IF EXISTS products CASCADE"))

            conn.execute(
                text(
                    "CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT, name TEXT, phone TEXT)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO users (email, name, phone) VALUES "
                    "('alice@example.com', 'Alice', '+1-555-0101'), "
                    "('bob@example.com', 'Bob', '+1-555-0102'), "
                    "('carol@example.com', 'Carol', '+1-555-0103')"
                )
            )

            conn.execute(
                text(
                    "CREATE TABLE orders ("
                    "id SERIAL PRIMARY KEY, "
                    "customer_id INTEGER, "
                    "total NUMERIC(10,2))"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO orders (customer_id, total) VALUES "
                    "(1, 50.00), (2, 75.50), (3, 99.99)"
                )
            )

            conn.execute(
                text(
                    "CREATE TABLE products ("
                    "id SERIAL PRIMARY KEY, "
                    "sku TEXT, "
                    "name TEXT, "
                    "price NUMERIC(10,2))"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO products (sku, name, price) VALUES "
                    "('W-001', 'Widget', 9.99), "
                    "('G-001', 'Gadget', 19.99)"
                )
            )
    finally:
        engine.dispose()

    print("Smoke DB ready: users, orders, products created in public schema.")


if __name__ == "__main__":
    main()
