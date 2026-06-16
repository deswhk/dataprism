"""SQLite implementation of the DatabaseAdapter protocol.

Built on SQLAlchemy Core for database-agnostic SQL construction. Despite
being SQLite-specific in connection handling, most of the actual SQL is
constructed via SQLAlchemy expressions that work on any database.

For testing: SqliteAdapter works against both file-based databases
(`sqlite:///path/to/db.sqlite`) and in-memory databases (`sqlite:///`
or `sqlite:///:memory:`).
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

# Silence SQLAlchemy's default query-dumping at INFO level.
# Without this, test output is buried under SQL spam.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


class SqliteAdapter:
    """Database adapter for SQLite.

    Uses SQLAlchemy Core for SQL construction. Doesn't open a connection
    until connect() is called; close() is required to release resources.

    Connection strings (DSNs):
        sqlite:///path/to/file.sqlite   - file-based, relative path
        sqlite:////abs/path/file.sqlite - file-based, absolute path
        sqlite:///:memory:              - in-memory (single-process)

    Also accepts a pathlib.Path object, which is normalized to a
    sqlite:/// DSN automatically.

    Example:
        from pathlib import Path
        adapter = SqliteAdapter()
        adapter.connect(Path("data.sqlite"))
        try:
            tables = adapter.list_tables()
            for table in tables:
                columns = adapter.list_columns(table.name)
                for col in columns:
                    samples = adapter.sample_values(table.name, col.name, n=1000)
                    print(f"{col.name}: {samples.sample_size_actual} values "
                          f"({samples.null_count} nulls)")
        finally:
            adapter.close()
    """

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._metadata: MetaData | None = None

    def connect(self, dsn: str | Path) -> None:
        """Open a connection to the SQLite database.

        Args:
            dsn: A SQLAlchemy DSN string or a Path. Paths are converted
                to file-based SQLite DSNs automatically.

        Raises:
            AdapterConnectionError: If the DSN is malformed or the
                database file cannot be opened.
        """
        dsn_str = self._normalize_dsn(dsn)
        try:
            self._engine = create_engine(dsn_str)
            # Force the connection to verify the DSN works.
            # SQLite is lazy by default - create_engine doesn't open
            # the file until something actually queries.
            with self._engine.connect():
                pass
            self._metadata = MetaData()
            self._metadata.reflect(bind=self._engine)
        except Exception as e:
            # Reset any partial state
            self._engine = None
            self._metadata = None
            raise AdapterConnectionError(
                f"Could not connect to SQLite database '{dsn_str}': {e}"
            ) from e

    def close(self) -> None:
        """Release the connection.

        Idempotent: calling on a not-connected adapter is a no-op.
        """
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
        self._metadata = None

    def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """List all tables in the database.

        SQLite doesn't have real schemas. The `schema` parameter is
        accepted for protocol consistency but ignored - we always
        call SQLAlchemy's get_table_names() with schema=None.

        Earlier versions passed the parameter through, which caused
        AdapterQueryError when callers passed any non-default value
        (SQLAlchemy interpreted it as a SQLite ATTACH DATABASE name).
        """
        self._require_connected()
        inspector = inspect(self._engine)
        try:
            # Always pass schema=None - SQLite has no schema concept,
            # and SQLAlchemy interprets non-None values as ATTACH names.
            names = inspector.get_table_names(schema=None)
        except Exception as e:
            raise AdapterQueryError(f"Could not list tables: {e}") from e
        return [TableInfo(name=name, schema_name=None) for name in names]

    def list_columns(self, table: str) -> list[ColumnInfo]:
        """List all columns of the given table."""
        self._require_connected()
        inspector = inspect(self._engine)
        if table not in inspector.get_table_names():
            raise AdapterQueryError(f"Table not found: {table}")
        try:
            cols = inspector.get_columns(table)
        except Exception as e:
            raise AdapterQueryError(f"Could not list columns of '{table}': {e}") from e
        return [
            ColumnInfo(
                name=col["name"],
                table=table,
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

        NULL handling:
        - The query selects all values including NULLs (no WHERE filter).
        - NULLs are counted (null_count) and kept in `typed` as None.
        - `text` excludes NULLs (cannot stringify None meaningfully).

        Sampling strategy:
        - SEQUENTIAL: select with LIMIT, no ORDER BY (fast, deterministic).
        - RANDOM: ORDER BY func.random(), then LIMIT (slow but rigorous).

        Returns a SampledValues record. sample_size_actual may be less
        than n if the table has fewer rows.
        """
        self._require_connected()

        if table not in self._metadata.tables:
            raise AdapterQueryError(f"Table not found: {table}")

        table_obj = self._metadata.tables[table]
        if column not in table_obj.c:
            raise AdapterQueryError(f"Column not found: {table}.{column}")

        col = table_obj.c[column]
        stmt = select(col)

        if strategy == SamplingStrategy.RANDOM:
            stmt = stmt.order_by(func.random())

        stmt = stmt.limit(n)

        try:
            with self._engine.connect() as conn:
                rows = conn.execute(stmt).fetchall()
        except Exception as e:
            raise AdapterQueryError(f"Failed to sample {table}.{column}: {e}") from e

        # Build the SampledValues result.
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
        """Raise if the adapter isn't connected. Internal helper."""
        if self._engine is None or self._metadata is None:
            raise AdapterError("Adapter not connected. Call connect() first.")

    @staticmethod
    def _normalize_dsn(dsn: str | Path) -> str:
        """Convert a DSN argument to a string SQLite URL.

        Accepts:
        - str: returned as-is (must already be a valid SQLAlchemy DSN)
        - Path: converted to 'sqlite:///<absolute path>'
        """
        if isinstance(dsn, Path):
            return f"sqlite:///{dsn.resolve()}"
        return dsn

    @staticmethod
    def _to_str(value: Any) -> str:
        """Convert a database value to a deterministic string representation.

        - bool: "true"/"false" (lowercase, avoid Python's "True"/"False")
        - datetime: ISO 8601 format, UTC if timezone-aware (normalized)
        - date: ISO 8601 format
        - other: str() conversion

        Never called on None (filtered upstream).
        """
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, datetime):
            # Tech debt item 3: timezone normalization
            # If the datetime is naive, leave it; if aware, normalize to UTC
            if value.tzinfo is not None:
                value = value.astimezone(timezone.utc)
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)
