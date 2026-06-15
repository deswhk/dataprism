"""DSN normalization and adapter selection for the CLI.

The CLI accepts user-friendly DSN prefixes (e.g., `postgresql://`).
SQLAlchemy requires driver-aware prefixes (e.g., `postgresql+psycopg://`).
This module translates the former to the latter so users don't have
to know about Python driver names.

Adapter selection works by prefix matching. Adding a new database
backend means adding one entry to the dispatch table.
"""

from __future__ import annotations

from collections.abc import Callable

from dataprism.adapters.postgres import PostgresAdapter
from dataprism.adapters.protocol import DatabaseAdapter
from dataprism.adapters.sqlite import SqliteAdapter

# Mapping from user-facing DSN prefix to (adapter factory, normalized prefix).
# The normalized prefix is what SQLAlchemy actually wants.
#
# For SQLite, there's no driver disambiguation needed - "sqlite://" works
# directly. For Postgres, we accept the standard "postgresql://" form and
# translate to "postgresql+psycopg://" so SQLAlchemy uses psycopg v3 (not
# the default psycopg2).
_DSN_DISPATCH: dict[str, tuple[Callable[[], DatabaseAdapter], str]] = {
    "sqlite://": (SqliteAdapter, "sqlite://"),
    "postgresql://": (PostgresAdapter, "postgresql+psycopg://"),
}


def normalize_dsn(dsn: str) -> str:
    """Translate a user-facing DSN to the form SQLAlchemy expects.

    Args:
        dsn: A user-supplied DSN string (e.g., from DATAPRISM_DSN env var).

    Returns:
        The normalized DSN that SQLAlchemy's create_engine() expects.
        For SQLite, returned unchanged. For PostgreSQL, the prefix is
        translated from `postgresql://` to `postgresql+psycopg://`.

    Raises:
        ValueError: If the DSN's prefix doesn't match any known adapter.
    """
    for user_prefix, (_, normalized_prefix) in _DSN_DISPATCH.items():
        if dsn.startswith(user_prefix):
            if user_prefix == normalized_prefix:
                return dsn  # No change needed
            return normalized_prefix + dsn[len(user_prefix) :]

    known = ", ".join(_DSN_DISPATCH.keys())
    raise ValueError(
        f"Unknown DSN scheme. Supported prefixes: {known}. "
        f"Got prefix: {dsn.split('://', 1)[0] if '://' in dsn else dsn[:20]}"
    )


def select_adapter(dsn: str) -> DatabaseAdapter:
    """Pick the right adapter for a user-supplied DSN.

    Args:
        dsn: A user-supplied DSN string. The prefix determines which
            adapter class is selected.

    Returns:
        A new, unconnected DatabaseAdapter instance. The caller is
        responsible for calling .connect() before use and .close() after.

    Raises:
        ValueError: If the DSN's prefix doesn't match any known adapter.
    """
    for prefix, (adapter_factory, _) in _DSN_DISPATCH.items():
        if dsn.startswith(prefix):
            return adapter_factory()

    known = ", ".join(_DSN_DISPATCH.keys())
    raise ValueError(
        f"Unknown DSN scheme. Supported prefixes: {known}. "
        f"Got prefix: {dsn.split('://', 1)[0] if '://' in dsn else dsn[:20]}"
    )
