"""PostgreSQL implementation of the DatabaseAdapter protocol.

Built on SQLAlchemy Core (same as SqliteAdapter), with the psycopg v3
driver. The actual SQL is largely the same; the differences are in
connection handling, schema awareness, and how Postgres-specific
type information surfaces.

DSN format:
    postgresql+psycopg://user:password@host:port/database

For example:
    postgresql+psycopg://postgres:secret@localhost:5432/mydb
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    MetaData,
    create_engine,
    func,
    inspect,
    select,
)
from sqlalchemy.engine import Engine

from dataprism.adapters.errors import (
    AdapterConnectionError,
    AdapterError,
    AdapterQueryError,
)
from dataprism.adapters.protocol import (
    ColumnInfo,
    SampledValues,
    SamplingStrategy,
    TableInfo,
)

# Silence SQLAlchemy's chatty INFO-level query logging.
# (SqliteAdapter does the same; both adapters configure the same logger,
# so the second call is a no-op but harmless.)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


class PostgresAdapter:
    """Database adapter for PostgreSQL.

    Uses SQLAlchemy Core with the psycopg v3 driver. Lifecycle follows
    the same pattern as SqliteAdapter:

        adapter = PostgresAdapter()
        adapter.connect("postgresql+psycopg://user:pass@host:5432/db")
        try:
            tables = adapter.list_tables(schema="public")  # schemas matter here
            for table in tables:
                columns = adapter.list_columns(table.name)
                for col in columns:
                    samples = adapter.sample_values(table.name, col.name, n=1000)
                    # ... pass to classification engine
        finally:
            adapter.close()

    Differences from SqliteAdapter:
    - The database must exist before connect() succeeds (Postgres does
      not auto-create databases like SQLite auto-creates files).
    - Network failures (unreachable host, bad credentials) raise
      AdapterConnectionError immediately at connect() time.
    - Schemas are real. list_tables() respects the schema parameter
      (defaults to "public" if None).
    - Column data_type values use Postgres conventions (e.g., "INTEGER",
      "TEXT", "TIMESTAMP" - the exact case depends on what SQLAlchemy's
      type compiler produces).
    """

    DEFAULT_SCHEMA = "public"

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._metadata: MetaData | None = None

    def connect(self, dsn: str | Path) -> None:
        """Open a connection to the PostgreSQL database.

        Args:
            dsn: A SQLAlchemy DSN string. Path is accepted for protocol
                consistency but unusual for Postgres - if a Path is
                passed, it's treated as a file containing the DSN
                (e.g., a secrets file).

        Raises:
            AdapterConnectionError: If the DSN is malformed, the host
                is unreachable, the credentials are wrong, or the
                database does not exist.
        """
        dsn_str = self._normalize_dsn(dsn)
        try:
            self._engine = create_engine(dsn_str)
            # Force the connection to verify the DSN works.
            # Unlike SQLite, this can raise for network/auth/db-existence
            # reasons. We want those errors at connect() time, not
            # during the first query.
            with self._engine.connect():
                pass
            self._metadata = MetaData()
        except Exception as e:
            # Reset any partial state
            self._engine = None
            self._metadata = None
            raise AdapterConnectionError(f"Could not connect to PostgreSQL: {e}") from e

    def close(self) -> None:
        """Release the connection.

        Idempotent: safe to call on a not-connected adapter.
        """
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
        self._metadata = None

    def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """List tables in the given schema (or 'public' if None).

        Postgres has real schemas; unlike SqliteAdapter which ignored
        the schema parameter, here it controls which tables are returned.
        """
        self._require_connected()
        schema = schema or self.DEFAULT_SCHEMA
        inspector = inspect(self._engine)
        try:
            names = inspector.get_table_names(schema=schema)
        except Exception as e:
            raise AdapterQueryError(f"Could not list tables in schema '{schema}': {e}") from e
        return [TableInfo(name=name, schema_name=schema) for name in names]

    def list_columns(self, table: str) -> list[ColumnInfo]:
        """List columns of the given table.

        Accepts either 'tablename' (implies 'public' schema) or
        'schema.tablename' (explicit schema). Splits on the first dot.
        """
        self._require_connected()
        schema, table_name = self._parse_table_ref(table)
        inspector = inspect(self._engine)
        existing = inspector.get_table_names(schema=schema)
        if table_name not in existing:
            raise AdapterQueryError(f"Table not found: {schema}.{table_name}")
        try:
            cols = inspector.get_columns(table_name, schema=schema)
        except Exception as e:
            raise AdapterQueryError(
                f"Could not list columns of '{schema}.{table_name}': {e}"
            ) from e
        return [
            ColumnInfo(
                name=col["name"],
                table=table_name,
                data_type=str(col["type"]),
                nullable=col.get("nullable", True),
            )
            for col in cols
        ]

    def sample_values(
        self,
        table: str,
        column: str,
        n: int = 1000,
        strategy: SamplingStrategy = SamplingStrategy.SEQUENTIAL,
    ) -> SampledValues:
        """Sample up to n values from a column.

        NULL handling and behavior match SqliteAdapter: NULLs are
        counted (null_count), preserved in `typed` as None, and
        filtered from `text`.

        Sampling strategy:
        - SEQUENTIAL: select with LIMIT, no ORDER BY (fast, but no
          guarantees about which rows in non-trivially-ordered tables).
        - RANDOM: ORDER BY func.random() before LIMIT (slow but
          statistically representative).
        """
        self._require_connected()
        schema, table_name = self._parse_table_ref(table)

        # Reflect this specific table on demand.
        # We use a fresh MetaData per call rather than caching, because
        # Postgres tables can change underneath us during a long-running
        # session.
        try:
            metadata = MetaData()
            from sqlalchemy import Table

            table_obj = Table(
                table_name,
                metadata,
                autoload_with=self._engine,
                schema=schema,
            )
        except Exception as e:
            raise AdapterQueryError(f"Table not found: {schema}.{table_name}: {e}") from e

        if column not in table_obj.c:
            raise AdapterQueryError(f"Column not found: {schema}.{table_name}.{column}")

        col = table_obj.c[column]
        stmt = select(col)

        if strategy == SamplingStrategy.RANDOM:
            stmt = stmt.order_by(func.random())

        stmt = stmt.limit(n)

        try:
            with self._engine.connect() as conn:
                rows = conn.execute(stmt).fetchall()
        except Exception as e:
            raise AdapterQueryError(f"Failed to sample {schema}.{table_name}.{column}: {e}") from e

        # Same logic as SqliteAdapter
        typed: list[Any] = [row[0] for row in rows]
        null_count = sum(1 for v in typed if v is None)
        text: list[str] = [self._to_str(v) for v in typed if v is not None]

        return SampledValues(
            text=text,
            typed=typed,
            null_count=null_count,
            sample_size_requested=n,
            sample_size_actual=len(typed),
        )

    def _require_connected(self) -> None:
        """Raise if not connected."""
        if self._engine is None:
            raise AdapterError("Adapter not connected. Call connect() first.")

    def _parse_table_ref(self, table: str) -> tuple[str, str]:
        """Parse 'schema.table' or 'table' into (schema, table_name)."""
        if "." in table:
            schema, table_name = table.split(".", 1)
            return schema, table_name
        return self.DEFAULT_SCHEMA, table

    @staticmethod
    def _normalize_dsn(dsn: str | Path) -> str:
        """Normalize a DSN. Paths are read as files (unusual but supported).

        For Postgres, paths don't make sense as connection targets
        directly. If a Path is given, we read its contents as the DSN
        - this supports the pattern of storing DSNs in secrets files.
        """
        if isinstance(dsn, Path):
            return dsn.read_text().strip()
        return dsn

    @staticmethod
    def _to_str(value: Any) -> str:
        """Convert a value to a deterministic string representation.

        Identical to SqliteAdapter._to_str. Could be extracted to a
        shared module if a third adapter needs the same logic.
        """
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.astimezone(timezone.utc)
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)
