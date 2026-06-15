"""Tests for dataprism.cli.adapters.

DSN normalization and adapter selection. Both functions share a
dispatch table; tests verify both branches (sqlite, postgresql)
and the error path (unknown prefix).
"""

from __future__ import annotations

import pytest

from dataprism.adapters.postgres import PostgresAdapter
from dataprism.adapters.sqlite import SqliteAdapter
from dataprism.cli.adapters import normalize_dsn, select_adapter


class TestNormalizeDsn:
    """Translate user-facing DSN prefixes to SQLAlchemy's driver-aware form."""

    def test_sqlite_dsn_unchanged(self):
        """SQLite DSN passes through unchanged - no driver disambiguation needed."""
        dsn = "sqlite:///./mydata.db"
        assert normalize_dsn(dsn) == dsn

    def test_sqlite_in_memory_unchanged(self):
        """SQLite in-memory DSN passes through unchanged."""
        dsn = "sqlite:///:memory:"
        assert normalize_dsn(dsn) == dsn

    def test_postgresql_gets_psycopg_prefix(self):
        """postgresql:// is rewritten to postgresql+psycopg:// for SQLAlchemy."""
        dsn = "postgresql://user:pass@host:5432/mydb"
        expected = "postgresql+psycopg://user:pass@host:5432/mydb"
        assert normalize_dsn(dsn) == expected

    def test_postgresql_preserves_user_password_host_path(self):
        """Only the prefix changes; everything after is preserved verbatim."""
        dsn = "postgresql://complex_user:p@ss/word@host.example.com:6543/some-db"
        result = normalize_dsn(dsn)
        # Verify the body after :// is untouched
        assert result.endswith("complex_user:p@ss/word@host.example.com:6543/some-db")

    def test_unknown_prefix_raises_value_error(self):
        """Unknown DSN scheme raises ValueError."""
        with pytest.raises(ValueError):
            normalize_dsn("mysql://user:pass@host/db")

    def test_unknown_prefix_error_message_lists_supported(self):
        """The error message names which prefixes are supported."""
        with pytest.raises(ValueError) as exc_info:
            normalize_dsn("mysql://user:pass@host/db")
        msg = str(exc_info.value)
        assert "sqlite://" in msg
        assert "postgresql://" in msg

    def test_unknown_prefix_error_does_not_leak_password(self):
        """The error message extracts only the prefix, not the full DSN."""
        with pytest.raises(ValueError) as exc_info:
            normalize_dsn("mysql://user:secret-password-123@host/db")
        msg = str(exc_info.value)
        assert "secret-password-123" not in msg

    def test_no_prefix_at_all_raises_value_error(self):
        """A string without :// raises ValueError too."""
        with pytest.raises(ValueError):
            normalize_dsn("not-a-dsn")


class TestSelectAdapter:
    """Pick the right adapter class based on DSN prefix."""

    def test_sqlite_dsn_returns_sqlite_adapter(self):
        """sqlite:// prefix selects SqliteAdapter."""
        adapter = select_adapter("sqlite:///./test.db")
        assert isinstance(adapter, SqliteAdapter)

    def test_sqlite_in_memory_returns_sqlite_adapter(self):
        """SQLite in-memory DSN also selects SqliteAdapter."""
        adapter = select_adapter("sqlite:///:memory:")
        assert isinstance(adapter, SqliteAdapter)

    def test_postgresql_dsn_returns_postgres_adapter(self):
        """postgresql:// prefix selects PostgresAdapter."""
        adapter = select_adapter("postgresql://user:pass@host:5432/db")
        assert isinstance(adapter, PostgresAdapter)

    def test_returns_new_unconnected_instance(self):
        """Each call returns a fresh adapter instance (not a singleton)."""
        adapter1 = select_adapter("sqlite:///./test.db")
        adapter2 = select_adapter("sqlite:///./test.db")
        assert adapter1 is not adapter2

    def test_unknown_prefix_raises_value_error(self):
        """Unknown DSN scheme raises ValueError."""
        with pytest.raises(ValueError):
            select_adapter("mysql://user:pass@host/db")

    def test_unknown_prefix_error_does_not_leak_password(self):
        """The error message extracts only the prefix, not the full DSN."""
        with pytest.raises(ValueError) as exc_info:
            select_adapter("redis://user:my-secret-key@host/0")
        msg = str(exc_info.value)
        assert "my-secret-key" not in msg
