"""Database adapter Protocol and supporting types.

Defines the contract that every database backend must satisfy:
- DatabaseAdapter: the Protocol (structural type)
- SamplingStrategy: enum for how to pick values
- SampledValues: rich container for sampling results
- TableInfo, ColumnInfo: metadata result types

This module is intentionally database-agnostic. Implementations
(SqliteAdapter, future PostgresAdapter) live in their own modules
and import this Protocol to satisfy it structurally.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class TableInfo(BaseModel):
    """Metadata about a table.

    Returned by DatabaseAdapter.list_tables(). Minimal in v2 -
    name plus optional schema. Future versions could add row counts,
    creation timestamps, etc.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    schema_name: str | None = None


class ColumnInfo(BaseModel):
    """Metadata about a column.

    Returned by DatabaseAdapter.list_columns(). Carries enough info
    for the classification engine to decide whether a rule applies
    (e.g., regex value-rules don't make sense on BOOLEAN columns).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    table: str
    data_type: str
    nullable: bool = True


class SamplingStrategy(StrEnum):
    """How to pick N values from a column.

    SEQUENTIAL: the first N values, in storage order. Fast, deterministic.
        The default for classification - we're asking "does this LOOK like PII?",
        which is robust to ordering.
    RANDOM: a random sample of N values. Slower (forces a full scan on most
        databases) but statistically representative. Use when you specifically
        want sample-level rigor.
    """

    SEQUENTIAL = "sequential"
    RANDOM = "random"


@dataclass(frozen=True)
class SampledValues:
    """The result of sampling a column.

    Carries multiple representations of the same data:
    - text: stringified, NULL values filtered out. What the classification
      engine consumes.
    - typed: native Python types from the database driver, NULLs preserved
      as None. What a future quality engine would consume for numeric and
      temporal operations.
    - null_count: how many NULLs were in the sample (not in `typed` length;
      `typed` includes them as None).
    - sample_size_requested: the n parameter that was asked for.
    - sample_size_actual: the count of `typed` (which may be less than n
      if the table has fewer rows).

    Frozen because results shouldn't be mutated after construction.

    Notes on consistency:
    - len(typed) == sample_size_actual (always)
    - len(text) == sample_size_actual - null_count (NULLs filtered out)
    - 0 <= null_count <= sample_size_actual
    """

    text: list[str]
    typed: list[Any]
    null_count: int
    sample_size_requested: int
    sample_size_actual: int


class DatabaseAdapter(Protocol):
    """Contract for any database backend.

    Implementations satisfy this protocol structurally (without inheritance).
    Engine code depends on this protocol, not on any specific adapter class.

    Lifecycle:
        adapter = SomeAdapter()         # construct (cheap, no I/O)
        adapter.connect(dsn)            # open connection (may fail)
        try:
            adapter.list_tables()       # use
            adapter.sample_values(...)  # use
        finally:
            adapter.close()             # release resources

    Adapter implementations are not thread-safe by default. Concurrent
    use requires the caller to coordinate.
    """

    def connect(self, dsn: str | Path) -> None:
        """Open a connection to the database.

        Args:
            dsn: A database connection string (SQLAlchemy DSN format)
                or, for file-based databases, a Path. The adapter
                normalizes accordingly.

        Raises:
            AdapterConnectionError: If the DSN is malformed, the
                database is unreachable, or authentication fails.
        """
        ...

    def close(self) -> None:
        """Release the connection and any associated resources.

        Idempotent: safe to call multiple times. Safe to call without
        a prior connect(). After close(), the adapter must be reconnected
        before further use.
        """
        ...

    def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """List tables visible to the connection.

        Args:
            schema: Optional schema name to filter by. None means
                "all visible tables" (typically the default schema).
                For databases without schema (SQLite), this argument
                may be ignored.

        Returns:
            List of TableInfo records, possibly empty.

        Raises:
            AdapterError: If not connected, or if schema query fails.
        """
        ...

    def list_columns(self, table: str) -> list[ColumnInfo]:
        """List columns of the given table.

        Args:
            table: Table name. Implementation should accept either
                "tablename" or "schema.tablename" (the latter only
                meaningful for databases that support schemas).

        Returns:
            List of ColumnInfo records in storage order.

        Raises:
            AdapterQueryError: If the table doesn't exist.
            AdapterError: If not connected.
        """
        ...

    def sample_values(
        self,
        table: str,
        column: str,
        n: int = 1000,
        strategy: SamplingStrategy = SamplingStrategy.SEQUENTIAL,
    ) -> SampledValues:
        """Sample up to n values from a column.

        Returns a SampledValues container with both string and typed
        representations. See SampledValues for the consistency
        guarantees between fields.

        Args:
            table: Table name.
            column: Column name.
            n: Maximum sample size. Default 1000.
            strategy: SEQUENTIAL (default, fast) or RANDOM (rigorous, slow).

        Returns:
            A SampledValues record.

        Raises:
            AdapterQueryError: If table or column doesn't exist.
            AdapterError: If not connected.
        """
        ...
