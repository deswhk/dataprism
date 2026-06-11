"""Tests for PostgresAdapter against a real PostgreSQL database.

These tests verify the Postgres-specific delta from SqliteAdapter:
- Connection lifecycle with real network errors
- Real schema support (not ignored like SQLite)
- Postgres data type name conventions
- Real BOOLEAN type handling
- NULL semantics verified against real Postgres (sanity check)

The Protocol contract itself is tested thoroughly in test_sqlite.py;
this file is intentionally focused on what's different.

Skipped entirely if DATAPRISM_TEST_POSTGRES_DSN environment variable
is not set. To run locally:
    $env:DATAPRISM_TEST_POSTGRES_DSN = "postgresql+psycopg://..."
    pytest tests/adapters/test_postgres.py
"""

import os

import pytest

from dataprism.adapters.errors import (
    AdapterConnectionError,
)
from dataprism.adapters.postgres import PostgresAdapter
from dataprism.adapters.protocol import SampledValues

from .fixtures import drop_postgres_test_table, make_postgres_test_table

POSTGRES_DSN = os.environ.get("DATAPRISM_TEST_POSTGRES_DSN")

# Skip all tests in this file if no DSN is provided.
# Defined at module level so pytest collects but skips quickly.
pytestmark = pytest.mark.skipif(
    POSTGRES_DSN is None,
    reason="DATAPRISM_TEST_POSTGRES_DSN not set; skipping Postgres tests",
)


@pytest.fixture
def test_table():
    """Create a uniquely-named test table; drop it after the test.

    Yields the table name. Cleanup runs even if the test fails.
    """
    table_name = make_postgres_test_table(POSTGRES_DSN, with_data=True)
    yield table_name
    drop_postgres_test_table(POSTGRES_DSN, table_name)


@pytest.fixture
def empty_table():
    """Create a uniquely-named empty test table; drop it after the test."""
    table_name = make_postgres_test_table(POSTGRES_DSN, with_data=False)
    yield table_name
    drop_postgres_test_table(POSTGRES_DSN, table_name)


class TestConnect:
    """Postgres-specific connection behaviors."""

    def test_valid_dsn_connects(self):
        adapter = PostgresAdapter()
        adapter.connect(POSTGRES_DSN)
        try:
            # If list_tables doesn't raise, the connection is alive
            tables = adapter.list_tables()
            assert isinstance(tables, list)
        finally:
            adapter.close()

    def test_unreachable_host_raises_connection_error(self):
        """Bad host raises AdapterConnectionError immediately at connect."""
        adapter = PostgresAdapter()
        bad_dsn = "postgresql+psycopg://postgres:any@unreachable.invalid:5432/x"
        with pytest.raises(AdapterConnectionError):
            adapter.connect(bad_dsn)

    def test_bad_credentials_raises_connection_error(self):
        """Wrong password raises AdapterConnectionError."""
        adapter = PostgresAdapter()
        # Construct a DSN with definitely-wrong password
        bad_dsn = POSTGRES_DSN.replace("postgres:", "postgres:wrong-password-")
        # If somehow the password contains the string we tried to inject,
        # this test is a no-op rather than a false positive
        if bad_dsn != POSTGRES_DSN:
            with pytest.raises(AdapterConnectionError):
                adapter.connect(bad_dsn)


class TestSchema:
    """Schema awareness - Postgres has real schemas, SQLite ignored them."""

    def test_list_tables_defaults_to_public_schema(self, test_table):
        """Without an explicit schema, returns tables from 'public'."""
        adapter = PostgresAdapter()
        adapter.connect(POSTGRES_DSN)
        try:
            tables = adapter.list_tables()  # No schema specified
            table_names = {t.name for t in tables}
            assert test_table in table_names
            # All tables should report 'public' as their schema
            our_table = next(t for t in tables if t.name == test_table)
            assert our_table.schema_name == "public"
        finally:
            adapter.close()

    def test_list_tables_with_explicit_public_schema(self, test_table):
        """Passing schema='public' explicitly works the same as default."""
        adapter = PostgresAdapter()
        adapter.connect(POSTGRES_DSN)
        try:
            tables = adapter.list_tables(schema="public")
            table_names = {t.name for t in tables}
            assert test_table in table_names
        finally:
            adapter.close()


class TestListColumns:
    """Column metadata against real Postgres."""

    def test_data_types_match_postgres_conventions(self, test_table):
        """Postgres reports specific type names through SQLAlchemy.

        Verifies what we discovered in smoke tests: types come back
        as INTEGER, TEXT, BOOLEAN, REAL (not Postgres internal names).
        """
        adapter = PostgresAdapter()
        adapter.connect(POSTGRES_DSN)
        try:
            columns = adapter.list_columns(test_table)
            by_name = {c.name: c for c in columns}
            assert by_name["id"].data_type == "INTEGER"
            assert by_name["email"].data_type == "TEXT"
            assert by_name["name"].data_type == "TEXT"
            assert by_name["active"].data_type == "BOOLEAN"
            assert by_name["score"].data_type == "REAL"
        finally:
            adapter.close()


class TestSampleValues:
    """NULL semantics and basic sampling against real Postgres."""

    def test_null_handling_matches_protocol(self, test_table):
        """NULLs filtered from text, preserved in typed - verified on real Postgres."""
        adapter = PostgresAdapter()
        adapter.connect(POSTGRES_DSN)
        try:
            result = adapter.sample_values(test_table, "name", n=10)
            assert isinstance(result, SampledValues)
            # 5 rows total, 1 NULL in 'name' column
            assert result.sample_size_actual == 5
            assert result.null_count == 1
            assert len(result.text) == 4  # NULL filtered
            assert len(result.typed) == 5  # NULL preserved
            assert None in result.typed
            assert None not in result.text
        finally:
            adapter.close()

    def test_empty_table_returns_empty_result(self, empty_table):
        """An empty table returns SampledValues with all zeros."""
        adapter = PostgresAdapter()
        adapter.connect(POSTGRES_DSN)
        try:
            result = adapter.sample_values(empty_table, "id", n=10)
            assert result.text == []
            assert result.typed == []
            assert result.null_count == 0
            assert result.sample_size_actual == 0
        finally:
            adapter.close()


class TestSampleValuesBooleans:
    """Postgres BOOLEAN type - distinct from SQLite which stored bools as INTEGERs."""

    def test_boolean_column_converts_to_lowercase_strings(self, test_table):
        """Real Postgres BOOLEANs become 'true'/'false' in text via _to_str.

        This is the test that justifies the bool branch in _to_str().
        SQLite stored 1/0 (integers); Postgres returns Python bool, which
        triggers the bool-handling branch.
        """
        adapter = PostgresAdapter()
        adapter.connect(POSTGRES_DSN)
        try:
            result = adapter.sample_values(test_table, "active", n=10)
            # 3 true, 2 false in the fixture data
            assert "true" in result.text
            assert "false" in result.text
            assert all(v in ("true", "false") for v in result.text)
            # typed should be actual Python booleans
            assert all(isinstance(v, bool) for v in result.typed)
        finally:
            adapter.close()
