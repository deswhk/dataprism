"""Tests for SqliteAdapter.

Uses pytest's tmp_path fixture for filesystem isolation - each test
gets its own temp directory that's cleaned up automatically. Test
databases are built by helpers in fixtures.py.

Tests are organized by method/concern. Each test class focuses on one
aspect of the adapter's contract.
"""

import pytest

from dataprism.adapters.errors import (
    AdapterConnectionError,
    AdapterError,
    AdapterQueryError,
)
from dataprism.adapters.protocol import SampledValues, SamplingStrategy
from dataprism.adapters.sqlite import SqliteAdapter

from .fixtures import (
    make_minimal_db,
    make_multi_table_db,
    make_users_db,
)


class TestConnect:
    """Connection lifecycle: connect() with various inputs."""

    def test_connect_with_str_dsn(self, tmp_path):
        dsn = make_minimal_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            assert adapter.list_tables() != []
        finally:
            adapter.close()

    def test_connect_with_path(self, tmp_path):
        """connect() accepts a Path and normalizes it to a DSN."""
        path = tmp_path / "test.db"
        make_minimal_db(path)
        adapter = SqliteAdapter()
        adapter.connect(path)
        try:
            assert adapter.list_tables() != []
        finally:
            adapter.close()

    def test_connect_nonexistent_file_does_not_raise(self, tmp_path):
        """SQLite creates the file if it doesn't exist.

        This is SQLite-specific behavior. Other adapters might raise.
        """
        path = tmp_path / "new.db"
        adapter = SqliteAdapter()
        adapter.connect(path)
        try:
            # File was created with no tables
            assert adapter.list_tables() == []
        finally:
            adapter.close()

    def test_connect_with_bad_dsn_raises_connection_error(self):
        adapter = SqliteAdapter()
        with pytest.raises(AdapterConnectionError):
            adapter.connect("not://a/valid/dsn")

    def test_connect_resets_partial_state_on_failure(self):
        """If connect() raises, the adapter must be in a clean state.

        Calling close() afterwards should not crash, and reconnecting
        should work.
        """
        adapter = SqliteAdapter()
        with pytest.raises(AdapterConnectionError):
            adapter.connect("not://a/valid/dsn")
        adapter.close()  # Must not raise

    def test_can_reconnect_after_close(self, tmp_path):
        dsn = make_minimal_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        adapter.close()
        adapter.connect(dsn)  # Must succeed
        try:
            assert adapter.list_tables() != []
        finally:
            adapter.close()


class TestClose:
    """close() behavior."""

    def test_close_releases_engine(self, tmp_path):
        dsn = make_minimal_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        adapter.close()
        # After close, the adapter must require reconnection
        with pytest.raises(AdapterError):
            adapter.list_tables()

    def test_close_is_idempotent(self, tmp_path):
        dsn = make_minimal_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        adapter.close()
        adapter.close()  # Second close must not raise

    def test_close_without_connect_is_safe(self):
        adapter = SqliteAdapter()
        adapter.close()  # Must not raise


class TestListTables:
    """list_tables() across various databases."""

    def test_minimal_database_one_table(self, tmp_path):
        dsn = make_minimal_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            tables = adapter.list_tables()
            assert len(tables) == 1
            assert tables[0].name == "widgets"
        finally:
            adapter.close()

    def test_multi_table_database(self, tmp_path):
        dsn = make_multi_table_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            tables = adapter.list_tables()
            names = {t.name for t in tables}
            assert names == {"customers", "orders", "products"}
        finally:
            adapter.close()

    def test_empty_database_returns_empty_list(self, tmp_path):
        path = tmp_path / "empty.db"
        adapter = SqliteAdapter()
        adapter.connect(path)
        try:
            assert adapter.list_tables() == []
        finally:
            adapter.close()

    def test_schema_argument_ignored_for_sqlite(self, tmp_path):
        """SQLite has no real schemas; the parameter is accepted but ignored.

        Earlier versions passed the parameter through to SQLAlchemy, which
        interpreted any non-default value as a SQLite ATTACH DATABASE name
        and raised. This test ensures that contract is preserved: passing
        any string for schema yields the same result as passing None.
        """
        dsn = make_minimal_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            # Baseline: default behavior
            baseline = adapter.list_tables(schema=None)
            assert len(baseline) == 1

            # Same result with an explicit (but meaningless) schema name
            with_schema = adapter.list_tables(schema="nonexistent")
            assert with_schema == baseline

            # And with a name that happens to match a Postgres convention
            with_public = adapter.list_tables(schema="public")
            assert with_public == baseline
        finally:
            adapter.close()


class TestListColumns:
    """list_columns() metadata extraction."""

    def test_returns_all_columns(self, tmp_path):
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            columns = adapter.list_columns("users")
            names = [c.name for c in columns]
            assert names == ["id", "email", "name", "active", "score"]
        finally:
            adapter.close()

    def test_column_has_table_name(self, tmp_path):
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            columns = adapter.list_columns("users")
            for col in columns:
                assert col.table == "users"
        finally:
            adapter.close()

    def test_data_type_is_string(self, tmp_path):
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            columns = adapter.list_columns("users")
            by_name = {c.name: c for c in columns}
            assert by_name["id"].data_type == "INTEGER"
            assert by_name["email"].data_type == "TEXT"
            assert by_name["score"].data_type == "REAL"
        finally:
            adapter.close()

    def test_nullable_default_is_true(self, tmp_path):
        """SQLite columns without NOT NULL are nullable."""
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            columns = adapter.list_columns("users")
            assert all(c.nullable for c in columns)
        finally:
            adapter.close()

    def test_missing_table_raises_query_error(self, tmp_path):
        dsn = make_minimal_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            with pytest.raises(AdapterQueryError):
                adapter.list_columns("nonexistent_table")
        finally:
            adapter.close()


class TestSampleValuesBasic:
    """sample_values() happy paths."""

    def test_returns_sampled_values_instance(self, tmp_path):
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            result = adapter.sample_values("users", "email", n=10)
            assert isinstance(result, SampledValues)
        finally:
            adapter.close()

    def test_returns_all_values_when_n_larger_than_rows(self, tmp_path):
        """If n > row count, returns all rows."""
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            result = adapter.sample_values("users", "email", n=100)
            assert result.sample_size_actual == 5
            assert result.sample_size_requested == 100
        finally:
            adapter.close()

    def test_respects_n_limit(self, tmp_path):
        """If n < row count, returns exactly n rows."""
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            result = adapter.sample_values("users", "email", n=2)
            assert result.sample_size_actual == 2
            assert len(result.text) == 2
        finally:
            adapter.close()

    def test_default_n_is_1000(self, tmp_path):
        """Default n parameter is 1000 (matches protocol default)."""
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            result = adapter.sample_values("users", "email")
            assert result.sample_size_requested == 1000
        finally:
            adapter.close()

    def test_empty_table_returns_empty_result(self, tmp_path):
        """Sampling an empty table returns an empty result."""
        path = tmp_path / "empty.db"
        adapter = SqliteAdapter()
        adapter.connect(path)
        try:
            # Create an empty table
            from sqlalchemy import create_engine, text

            engine = create_engine(f"sqlite:///{path}")
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE t (x INTEGER)"))
            engine.dispose()
            # Reconnect to pick up the new table
            adapter.close()
            adapter.connect(path)

            result = adapter.sample_values("t", "x", n=10)
            assert result.text == []
            assert result.typed == []
            assert result.null_count == 0
            assert result.sample_size_actual == 0
        finally:
            adapter.close()


class TestSampleValuesNulls:
    """NULL handling in sample_values()."""

    def test_nulls_counted_in_null_count(self, tmp_path):
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            # users.name has 1 NULL (Bob's row)
            result = adapter.sample_values("users", "name", n=10)
            assert result.null_count == 1
        finally:
            adapter.close()

    def test_nulls_excluded_from_text(self, tmp_path):
        """text field has NULLs filtered out."""
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            result = adapter.sample_values("users", "name", n=10)
            # 5 rows, 1 NULL → 4 strings
            assert len(result.text) == 4
            assert None not in result.text
        finally:
            adapter.close()

    def test_nulls_preserved_in_typed(self, tmp_path):
        """typed field preserves None for NULL rows."""
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            result = adapter.sample_values("users", "name", n=10)
            # 5 rows total in typed, including 1 None
            assert len(result.typed) == 5
            assert None in result.typed
        finally:
            adapter.close()

    def test_all_null_column(self, tmp_path):
        """A column with all NULLs returns text=[] and typed=[None, None, ...]."""
        path = tmp_path / "test.db"
        from sqlalchemy import create_engine, text

        engine = create_engine(f"sqlite:///{path}")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE t (x TEXT)"))
            conn.execute(text("INSERT INTO t VALUES (NULL)"))
            conn.execute(text("INSERT INTO t VALUES (NULL)"))
        engine.dispose()

        adapter = SqliteAdapter()
        adapter.connect(path)
        try:
            result = adapter.sample_values("t", "x", n=10)
            assert result.text == []
            assert result.typed == [None, None]
            assert result.null_count == 2
            assert result.sample_size_actual == 2
        finally:
            adapter.close()


class TestSampleValuesTypes:
    """Type conversion in text vs typed fields."""

    def test_text_column_returns_strings(self, tmp_path):
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            result = adapter.sample_values("users", "email", n=10)
            assert all(isinstance(v, str) for v in result.text)
        finally:
            adapter.close()

    def test_integer_column_converted_to_string_in_text(self, tmp_path):
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            result = adapter.sample_values("users", "id", n=10)
            assert all(isinstance(v, str) for v in result.text)
            # Integers preserved as ints in typed
            assert all(isinstance(v, int) for v in result.typed)
        finally:
            adapter.close()

    def test_real_column_converted_correctly(self, tmp_path):
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            result = adapter.sample_values("users", "score", n=10)
            # Text should be string representations
            assert all(isinstance(v, str) for v in result.text)
            # Typed should be floats
            non_null_typed = [v for v in result.typed if v is not None]
            assert all(isinstance(v, float) for v in non_null_typed)
        finally:
            adapter.close()

    def test_typed_count_matches_sample_size_actual(self, tmp_path):
        """The contract: len(typed) == sample_size_actual."""
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            result = adapter.sample_values("users", "name", n=10)
            assert len(result.typed) == result.sample_size_actual
        finally:
            adapter.close()


class TestSampleValuesStrategy:
    """Sampling strategy behavior."""

    def test_sequential_default(self, tmp_path):
        """SEQUENTIAL is the default strategy."""
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            r1 = adapter.sample_values("users", "id", n=10)
            r2 = adapter.sample_values("users", "id", n=10, strategy=SamplingStrategy.SEQUENTIAL)
            assert r1.typed == r2.typed
        finally:
            adapter.close()

    def test_sequential_is_deterministic(self, tmp_path):
        """SEQUENTIAL returns the same rows in the same order each call."""
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            r1 = adapter.sample_values("users", "id", n=10, strategy=SamplingStrategy.SEQUENTIAL)
            r2 = adapter.sample_values("users", "id", n=10, strategy=SamplingStrategy.SEQUENTIAL)
            assert r1.typed == r2.typed
        finally:
            adapter.close()

    def test_random_returns_same_set_different_order_sometimes(self, tmp_path):
        """RANDOM returns the same rows but possibly in different order.

        We test the set is preserved, not the order. With only 5 rows
        we always get all of them, just possibly shuffled.
        """
        dsn = make_users_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            result = adapter.sample_values("users", "id", n=10, strategy=SamplingStrategy.RANDOM)
            # Should still get all 5 ids
            assert set(result.typed) == {1, 2, 3, 4, 5}
        finally:
            adapter.close()


class TestSampleValuesErrors:
    """Error paths in sample_values()."""

    def test_missing_table_raises(self, tmp_path):
        dsn = make_minimal_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            with pytest.raises(AdapterQueryError):
                adapter.sample_values("nonexistent_table", "x", n=10)
        finally:
            adapter.close()

    def test_missing_column_raises(self, tmp_path):
        dsn = make_minimal_db(tmp_path / "test.db")
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            with pytest.raises(AdapterQueryError):
                adapter.sample_values("widgets", "nonexistent_column", n=10)
        finally:
            adapter.close()


class TestRequireConnected:
    """All public methods must raise when called before connect()."""

    def test_list_tables_requires_connection(self):
        adapter = SqliteAdapter()
        with pytest.raises(AdapterError):
            adapter.list_tables()

    def test_list_columns_requires_connection(self):
        adapter = SqliteAdapter()
        with pytest.raises(AdapterError):
            adapter.list_columns("any_table")

    def test_sample_values_requires_connection(self):
        adapter = SqliteAdapter()
        with pytest.raises(AdapterError):
            adapter.sample_values("any_table", "any_column", n=10)
