"""Tests for the high-level classify_table function.

The classify_table function combines a DatabaseAdapter (we use
SqliteAdapter for speed - the Protocol contract is validated in
test_sqlite.py and test_postgres.py), a ClassificationPolicy, and
an AuditService into a single function that classifies every column
in a table.

These tests verify:
- The happy path: all columns classify, the report is well-formed
- Audit instrumentation: STARTED/COMPLETED bookends emit correctly
- Pydantic constraints: extra="forbid", frozen=True on the report
- Error handling: AdapterError on list_columns propagates; AdapterError
  on per-column sampling is caught and recorded in the report
- Sampling parameters: defaults applied; custom values propagate
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from dataprism.adapters.errors import AdapterQueryError
from dataprism.adapters.protocol import (
    SampledValues,
    SamplingStrategy,
)
from dataprism.adapters.sqlite import SqliteAdapter
from dataprism.audit.events import EventType
from dataprism.audit.service import AuditService
from dataprism.audit.storage import InMemoryStorage
from dataprism.classification.table import (
    FailedTable,
    ScanReport,
    TableClassificationReport,
    classify_table,
    classify_tables,
)
from dataprism.policy.models import (
    ClassificationLabel,
    ClassificationPolicy,
    DictionaryMatchMode,
    DictionaryRule,
    RegexRule,
    RegexTarget,
)

# ---- Test helpers ---------------------------------------------------
#
# NOTE: These three helpers (_make_engine, _dict_rule, _regex_rule)
# duplicate the helpers in test_engine.py. This is intentional - we
# chose duplication over a shared fixtures module to keep PR 9 (the
# classify_table API) focused and avoid touching test_engine.py.
#
# Future refactor: if/when a third classification test file needs the
# same helpers, extract them to tests/classification/fixtures.py and
# update both this file and test_engine.py to import from there.
# Documented in docs/ARCHITECTURE.md Section 8 "Test helper
# consolidation".


def _make_audit_setup(
    actor: str = "classify_table",
) -> tuple[AuditService, InMemoryStorage]:
    """Return (AuditService, InMemoryStorage) for inspecting audit events."""
    storage = InMemoryStorage()
    audit = AuditService(storage)
    return audit, storage


def _dict_rule(
    name: str,
    values: list[str],
    classification: ClassificationLabel = ClassificationLabel.PII,
) -> DictionaryRule:
    return DictionaryRule(
        type="dictionary",
        name=name,
        values=values,
        match_mode=DictionaryMatchMode.EXACT_NORMALIZED,
        classification=classification,
    )


def _regex_rule(
    name: str,
    target: RegexTarget,
    pattern: str,
    classification: ClassificationLabel = ClassificationLabel.PII,
) -> RegexRule:
    return RegexRule(
        type="regex",
        name=name,
        target=target,
        pattern=pattern,
        classification=classification,
    )


def _make_users_db(tmp_path) -> str:
    """Create a SQLite users database for classification tests.

    Local copy of make_users_db from tests/adapters/fixtures.py.
    Duplicated here because pytest test subpackages don't share
    fixtures (no tests/__init__.py). Documented in docs/ARCHITECTURE.md
    Section 8 'Test helper consolidation'.

    Schema:
        users (id INTEGER, email TEXT, name TEXT, active INTEGER, score REAL)
        - 5 rows, one with NULL name and NULL score
    """
    from sqlalchemy import create_engine, text

    dsn = f"sqlite:///{tmp_path / 'test.db'}"
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


def _make_multi_table_db(tmp_path) -> str:
    """Create a SQLite database with multiple tables for multi-table tests.

    Schema:
        users (id, email, name, active, score)         - 5 rows
        orders (id, customer_id, total)                - 3 rows
        products (id, sku, name, price)                - 3 rows
    """
    from sqlalchemy import create_engine, text

    dsn = f"sqlite:///{tmp_path / 'multi.db'}"
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            # users
            conn.execute(
                text(
                    "CREATE TABLE users ("
                    "id INTEGER, email TEXT, name TEXT, "
                    "active INTEGER, score REAL)"
                )
            )
            conn.execute(text("INSERT INTO users VALUES (1, 'a@example.com', 'Alice', 1, 99.5)"))
            conn.execute(text("INSERT INTO users VALUES (2, 'b@example.com', 'Bob', 1, 88.0)"))
            conn.execute(text("INSERT INTO users VALUES (3, 'c@example.com', 'Carol', 1, 77.5)"))
            conn.execute(text("INSERT INTO users VALUES (4, 'd@example.com', 'Dan', 0, 66.0)"))
            conn.execute(text("INSERT INTO users VALUES (5, 'e@example.com', 'Eve', 1, 55.5)"))

            # orders
            conn.execute(text("CREATE TABLE orders (id INTEGER, customer_id INTEGER, total REAL)"))
            conn.execute(text("INSERT INTO orders VALUES (1, 1, 49.99)"))
            conn.execute(text("INSERT INTO orders VALUES (2, 2, 19.50)"))
            conn.execute(text("INSERT INTO orders VALUES (3, 3, 75.00)"))

            # products
            conn.execute(
                text("CREATE TABLE products (id INTEGER, sku TEXT, name TEXT, price REAL)")
            )
            conn.execute(text("INSERT INTO products VALUES (1, 'A-001', 'Widget', 9.99)"))
            conn.execute(text("INSERT INTO products VALUES (2, 'A-002', 'Gadget', 19.99)"))
            conn.execute(text("INSERT INTO products VALUES (3, 'A-003', 'Sprocket', 4.49)"))
    finally:
        engine.dispose()
    return dsn


def _make_policy(rules: list) -> ClassificationPolicy:
    return ClassificationPolicy(version=1, classifiers=rules)


def _connect_sqlite(tmp_path) -> SqliteAdapter:
    """Create a SqliteAdapter against a users database and return it connected."""
    dsn = _make_users_db(tmp_path)  # changed from make_users_db
    adapter = SqliteAdapter()
    adapter.connect(dsn)
    return adapter


class TestClassifyTableHappyPath:
    """Verify the happy path: all columns classify, report is well-formed."""

    def test_returns_table_classification_report(self, tmp_path):
        """classify_table returns a TableClassificationReport."""
        adapter = _connect_sqlite(tmp_path)
        try:
            policy = _make_policy([_dict_rule("email_columns", ["email"])])
            audit, _ = _make_audit_setup()

            result = classify_table(adapter, "users", policy, audit)

            assert isinstance(result, TableClassificationReport)
            assert result.table == "users"
        finally:
            adapter.close()

    def test_columns_attempted_matches_table(self, tmp_path):
        """columns_attempted equals the number of columns in the table."""
        adapter = _connect_sqlite(tmp_path)
        try:
            # users table has 5 columns: id, email, name, active, score
            policy = _make_policy([_dict_rule("anything", ["x"])])
            audit, _ = _make_audit_setup()

            result = classify_table(adapter, "users", policy, audit)

            assert result.columns_attempted == 5
        finally:
            adapter.close()

    def test_matches_by_column_includes_all_columns(self, tmp_path):
        """Every column appears in matches_by_column (empty list if no matches)."""
        adapter = _connect_sqlite(tmp_path)
        try:
            policy = _make_policy([_dict_rule("email_columns", ["email"])])
            audit, _ = _make_audit_setup()

            result = classify_table(adapter, "users", policy, audit)

            expected_columns = {"id", "email", "name", "active", "score"}
            assert set(result.matches_by_column.keys()) == expected_columns
        finally:
            adapter.close()

    def test_matching_column_has_results(self, tmp_path):
        """A column matching a rule produces non-empty results."""
        adapter = _connect_sqlite(tmp_path)
        try:
            policy = _make_policy(
                [
                    _dict_rule("email_columns", ["email"], ClassificationLabel.PII),
                ]
            )
            audit, _ = _make_audit_setup()

            result = classify_table(adapter, "users", policy, audit)

            assert len(result.matches_by_column["email"]) == 1
            assert result.matches_by_column["email"][0].rule_name == "email_columns"
        finally:
            adapter.close()

    def test_non_matching_column_has_empty_results(self, tmp_path):
        """A column with no rules matching produces an empty list."""
        adapter = _connect_sqlite(tmp_path)
        try:
            policy = _make_policy([_dict_rule("email_columns", ["email"])])
            audit, _ = _make_audit_setup()

            result = classify_table(adapter, "users", policy, audit)

            # 'id' doesn't match 'email' as a column name
            assert result.matches_by_column["id"] == []
        finally:
            adapter.close()

    def test_no_errors_on_clean_run(self, tmp_path):
        """A successful run returns an empty errors list."""
        adapter = _connect_sqlite(tmp_path)
        try:
            policy = _make_policy([_dict_rule("email_columns", ["email"])])
            audit, _ = _make_audit_setup()

            result = classify_table(adapter, "users", policy, audit)

            assert result.errors == []
        finally:
            adapter.close()


class TestAuditEvents:
    """Verify the audit instrumentation: STARTED/COMPLETED bookends + per-column events."""

    def test_emits_started_event(self, tmp_path):
        """TABLE_CLASSIFICATION_STARTED is emitted at the start."""
        adapter = _connect_sqlite(tmp_path)
        try:
            policy = _make_policy([_dict_rule("anything", ["x"])])
            audit, storage = _make_audit_setup()

            classify_table(adapter, "users", policy, audit)

            events = list(storage.read_all())
            assert events[0].event_type == EventType.TABLE_CLASSIFICATION_STARTED
            assert events[0].data["table"] == "users"
            assert events[0].data["columns_count"] == 5
        finally:
            adapter.close()

    def test_emits_completed_event(self, tmp_path):
        """TABLE_CLASSIFICATION_COMPLETED is emitted at the end."""
        adapter = _connect_sqlite(tmp_path)
        try:
            policy = _make_policy([_dict_rule("anything", ["x"])])
            audit, storage = _make_audit_setup()

            classify_table(adapter, "users", policy, audit)

            events = list(storage.read_all())
            assert events[-1].event_type == EventType.TABLE_CLASSIFICATION_COMPLETED
            assert events[-1].data["table"] == "users"
            assert events[-1].data["columns_succeeded"] == 5
            assert events[-1].data["columns_failed"] == 0
        finally:
            adapter.close()

    def test_emits_per_column_classification_run_events(self, tmp_path):
        """Each successful column emits a CLASSIFICATION_RUN event (from the engine)."""
        adapter = _connect_sqlite(tmp_path)
        try:
            policy = _make_policy([_dict_rule("anything", ["x"])])
            audit, storage = _make_audit_setup()

            classify_table(adapter, "users", policy, audit)

            events = list(storage.read_all())
            classification_run_events = [
                e for e in events if e.event_type == EventType.CLASSIFICATION_RUN
            ]
            # 5 columns, 5 events
            assert len(classification_run_events) == 5

            # Each event has a column_name in its data
            column_names_in_events = {e.data["column_name"] for e in classification_run_events}
            assert column_names_in_events == {
                "id",
                "email",
                "name",
                "active",
                "score",
            }
        finally:
            adapter.close()

    def test_event_order_bookend_pattern(self, tmp_path):
        """Events follow the order: STARTED, [per-column], COMPLETED."""
        adapter = _connect_sqlite(tmp_path)
        try:
            policy = _make_policy([_dict_rule("anything", ["x"])])
            audit, storage = _make_audit_setup()

            classify_table(adapter, "users", policy, audit)

            events = list(storage.read_all())
            # First is STARTED, last is COMPLETED
            assert events[0].event_type == EventType.TABLE_CLASSIFICATION_STARTED
            assert events[-1].event_type == EventType.TABLE_CLASSIFICATION_COMPLETED
            # Everything in between is per-column
            middle_event_types = {e.event_type for e in events[1:-1]}
            assert middle_event_types == {EventType.CLASSIFICATION_RUN}
        finally:
            adapter.close()

    def test_actor_propagates_to_all_events(self, tmp_path):
        """A custom actor appears on all events (STARTED, per-column, COMPLETED)."""
        adapter = _connect_sqlite(tmp_path)
        try:
            policy = _make_policy([_dict_rule("anything", ["x"])])
            audit, storage = _make_audit_setup()

            classify_table(adapter, "users", policy, audit, actor="custom-actor")

            events = list(storage.read_all())
            actors = {e.actor for e in events}
            assert actors == {"custom-actor"}
        finally:
            adapter.close()

    def test_default_actor_is_classify_table(self, tmp_path):
        """Without explicit actor, events have actor='classify_table'."""
        adapter = _connect_sqlite(tmp_path)
        try:
            policy = _make_policy([_dict_rule("anything", ["x"])])
            audit, storage = _make_audit_setup()

            classify_table(adapter, "users", policy, audit)

            events = list(storage.read_all())
            actors = {e.actor for e in events}
            assert actors == {"classify_table"}
        finally:
            adapter.close()


class TestReportShape:
    """Verify TableClassificationReport's Pydantic constraints."""

    def test_report_is_frozen(self):
        """TableClassificationReport is immutable."""
        report = TableClassificationReport(
            table="users",
            columns_attempted=5,
            matches_by_column={},
            errors=[],
        )
        with pytest.raises(ValidationError):
            report.table = "other"  # type: ignore[misc]

    def test_report_rejects_unknown_fields(self):
        """extra='forbid' on the report rejects unexpected fields."""
        with pytest.raises(ValidationError):
            TableClassificationReport(
                table="users",
                columns_attempted=5,
                matches_by_column={},
                errors=[],
                unknown_field="oops",  # type: ignore[call-arg]
            )

    def test_consistency_invariant(self, tmp_path):
        """len(matches_by_column) + len(errors) == columns_attempted."""
        adapter = _connect_sqlite(tmp_path)
        try:
            policy = _make_policy([_dict_rule("anything", ["x"])])
            audit, _ = _make_audit_setup()

            result = classify_table(adapter, "users", policy, audit)

            assert len(result.matches_by_column) + len(result.errors) == result.columns_attempted
        finally:
            adapter.close()


class TestErrorHandling:
    """Verify error propagation and per-column error collection."""

    def test_list_columns_failure_propagates(self, tmp_path):
        """If list_columns fails, classify_table raises and emits NO audit events."""

        class BrokenListColumnsAdapter:
            """Adapter that raises on list_columns."""

            def connect(self, dsn):
                pass

            def close(self):
                pass

            def list_tables(self, schema=None):
                return []

            def list_columns(self, table):
                raise AdapterQueryError(f"Table not found: {table}")

            def sample_values(self, table, column, n=1000, strategy=None):
                raise AssertionError("Should not be called")

        policy = _make_policy([_dict_rule("anything", ["x"])])
        audit, storage = _make_audit_setup()

        with pytest.raises(AdapterQueryError):
            classify_table(
                BrokenListColumnsAdapter(),  # type: ignore[arg-type]
                "users",
                policy,
                audit,
            )

        # No audit events emitted - failure happened before STARTED could fire
        events = list(storage.read_all())
        assert events == []

    def test_per_column_sample_failure_collected_in_errors(self, tmp_path):
        """An AdapterError during sample_values is caught and recorded."""
        from dataprism.adapters.protocol import ColumnInfo

        class BrokenSampleAdapter:
            """Adapter that raises on sample_values for the 'name' column only."""

            def connect(self, dsn):
                pass

            def close(self):
                pass

            def list_tables(self, schema=None):
                return []

            def list_columns(self, table):
                return [
                    ColumnInfo(
                        name="id",
                        table=table,
                        data_type="INTEGER",
                        nullable=True,
                    ),
                    ColumnInfo(
                        name="name",
                        table=table,
                        data_type="TEXT",
                        nullable=True,
                    ),
                ]

            def sample_values(self, table, column, n=1000, strategy=None):
                if column == "name":
                    raise AdapterQueryError("Synthetic failure on 'name'")
                return SampledValues(
                    text=["value"],
                    typed=["value"],
                    null_count=0,
                    sample_size_requested=n,
                    sample_size_actual=1,
                )

        policy = _make_policy([_dict_rule("anything", ["x"])])
        audit, storage = _make_audit_setup()

        result = classify_table(
            BrokenSampleAdapter(),  # type: ignore[arg-type]
            "users",
            policy,
            audit,
        )

        # 'id' succeeded; 'name' failed
        assert result.columns_attempted == 2
        assert "id" in result.matches_by_column
        assert "name" not in result.matches_by_column
        assert len(result.errors) == 1
        assert result.errors[0].column_name == "name"
        assert "Synthetic failure" in result.errors[0].error

        # Audit log shows: STARTED + CLASSIFICATION_RUN(id) +
        # CLASSIFICATION_FAILED(name) + COMPLETED
        events = list(storage.read_all())
        event_types = [e.event_type for e in events]
        assert event_types == [
            EventType.TABLE_CLASSIFICATION_STARTED,
            EventType.CLASSIFICATION_RUN,
            EventType.CLASSIFICATION_FAILED,
            EventType.TABLE_CLASSIFICATION_COMPLETED,
        ]

        # COMPLETED event reflects the counts
        completed = events[-1]
        assert completed.data["columns_succeeded"] == 1
        assert completed.data["columns_failed"] == 1


class TestSamplingParameters:
    """Verify sample_size and strategy parameters propagate to the adapter."""

    def test_default_sample_size_is_1000(self, tmp_path):
        """Default sample_size=1000 is forwarded to sample_values."""
        captured = []

        class CapturingAdapter:
            """Adapter that records what sample_values was called with."""

            def connect(self, dsn):
                pass

            def close(self):
                pass

            def list_tables(self, schema=None):
                return []

            def list_columns(self, table):
                from dataprism.adapters.protocol import ColumnInfo

                return [
                    ColumnInfo(name="x", table=table, data_type="TEXT", nullable=True),
                ]

            def sample_values(self, table, column, n=1000, strategy=None):
                captured.append({"n": n, "strategy": strategy})
                return SampledValues(
                    text=[],
                    typed=[],
                    null_count=0,
                    sample_size_requested=n,
                    sample_size_actual=0,
                )

        policy = _make_policy([_dict_rule("anything", ["x"])])
        audit, _ = _make_audit_setup()

        classify_table(
            CapturingAdapter(),  # type: ignore[arg-type]
            "users",
            policy,
            audit,
        )

        assert captured[0]["n"] == 1000

    def test_default_strategy_is_sequential(self, tmp_path):
        """Default strategy=SamplingStrategy.SEQUENTIAL is forwarded."""
        captured = []

        class CapturingAdapter:
            def connect(self, dsn):
                pass

            def close(self):
                pass

            def list_tables(self, schema=None):
                return []

            def list_columns(self, table):
                from dataprism.adapters.protocol import ColumnInfo

                return [
                    ColumnInfo(name="x", table=table, data_type="TEXT", nullable=True),
                ]

            def sample_values(self, table, column, n=1000, strategy=None):
                captured.append({"n": n, "strategy": strategy})
                return SampledValues(
                    text=[],
                    typed=[],
                    null_count=0,
                    sample_size_requested=n,
                    sample_size_actual=0,
                )

        policy = _make_policy([_dict_rule("anything", ["x"])])
        audit, _ = _make_audit_setup()

        classify_table(
            CapturingAdapter(),  # type: ignore[arg-type]
            "users",
            policy,
            audit,
        )

        assert captured[0]["strategy"] == SamplingStrategy.SEQUENTIAL

    def test_custom_parameters_propagate(self, tmp_path):
        """Custom sample_size and strategy are forwarded to sample_values."""
        captured = []

        class CapturingAdapter:
            def connect(self, dsn):
                pass

            def close(self):
                pass

            def list_tables(self, schema=None):
                return []

            def list_columns(self, table):
                from dataprism.adapters.protocol import ColumnInfo

                return [
                    ColumnInfo(name="x", table=table, data_type="TEXT", nullable=True),
                ]

            def sample_values(self, table, column, n=1000, strategy=None):
                captured.append({"n": n, "strategy": strategy})
                return SampledValues(
                    text=[],
                    typed=[],
                    null_count=0,
                    sample_size_requested=n,
                    sample_size_actual=0,
                )

        policy = _make_policy([_dict_rule("anything", ["x"])])
        audit, _ = _make_audit_setup()

        classify_table(
            CapturingAdapter(),  # type: ignore[arg-type]
            "users",
            policy,
            audit,
            sample_size=42,
            strategy=SamplingStrategy.RANDOM,
        )

        assert captured[0]["n"] == 42
        assert captured[0]["strategy"] == SamplingStrategy.RANDOM


# =====================================================================
# Tests for classify_tables
# =====================================================================


class TestClassifyTablesHappyPath:
    """Multi-table classify_tables happy path - all tables succeed."""

    def test_returns_scan_result(self, tmp_path):
        """classify_tables returns a ScanReport."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["users", "orders", "products"], policy, audit)
            assert isinstance(result, ScanReport)
        finally:
            adapter.close()

    def test_tables_list_has_one_report_per_input(self, tmp_path):
        """All three input tables produce a TableClassificationReport."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["users", "orders", "products"], policy, audit)
            assert len(result.tables) == 3
            table_names = {r.table for r in result.tables}
            assert table_names == {"users", "orders", "products"}
        finally:
            adapter.close()

    def test_no_failures_on_clean_run(self, tmp_path):
        """failed_tables is empty when all tables succeed."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["users", "orders", "products"], policy, audit)
            assert result.failed_tables == []
        finally:
            adapter.close()

    def test_each_report_carries_its_table_name(self, tmp_path):
        """Each TableClassificationReport's `table` field matches input."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["users", "orders"], policy, audit)
            # Order is not guaranteed by the function's contract, but
            # the names should round-trip.
            names = [r.table for r in result.tables]
            assert sorted(names) == ["orders", "users"]
        finally:
            adapter.close()

    def test_only_matching_table_finds_classification(self, tmp_path):
        """A policy targeting only 'email' classifies only the users table."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["users", "orders", "products"], policy, audit)

            # Find each report by name
            by_name = {r.table: r for r in result.tables}
            users_matches = sum(
                1 for matches in by_name["users"].matches_by_column.values() if matches
            )
            orders_matches = sum(
                1 for matches in by_name["orders"].matches_by_column.values() if matches
            )
            assert users_matches == 1  # email column
            assert orders_matches == 0
        finally:
            adapter.close()


class TestClassifyTablesMetadata:
    """Metadata fields on the returned ScanReport are populated correctly."""

    def test_scan_id_is_a_string(self, tmp_path):
        """scan_id is populated and is a non-empty string."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["users"], policy, audit)
            assert isinstance(result.scan_id, str)
            assert len(result.scan_id) > 0
        finally:
            adapter.close()

    def test_scan_id_matches_audit_event_scan_id(self, tmp_path):
        """The scan_id on the ScanReport matches the one in audit events.

        This is the cross-reference assertion: a renderered report
        and the audit log can be tied together via scan_id.
        """
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, storage = _make_audit_setup()
            result = classify_tables(adapter, ["users"], policy, audit)

            # Find SCAN_STARTED in the audit log; its scan_id must match.
            events = list(storage.read_all())
            scan_started = [e for e in events if e.event_type == EventType.SCAN_STARTED]
            assert len(scan_started) == 1
            assert scan_started[0].data["scan_id"] == result.scan_id

            scan_completed = [e for e in events if e.event_type == EventType.SCAN_COMPLETED]
            assert len(scan_completed) == 1
            assert scan_completed[0].data["scan_id"] == result.scan_id
        finally:
            adapter.close()

    def test_each_call_produces_unique_scan_id(self, tmp_path):
        """Two consecutive classify_tables calls produce different scan_ids."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            r1 = classify_tables(adapter, ["users"], policy, audit)
            r2 = classify_tables(adapter, ["users"], policy, audit)
            assert r1.scan_id != r2.scan_id
        finally:
            adapter.close()

    def test_started_at_is_before_completed_at(self, tmp_path):
        """started_at <= completed_at; both are UTC datetimes."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["users"], policy, audit)
            assert isinstance(result.started_at, datetime)
            assert isinstance(result.completed_at, datetime)
            assert result.started_at <= result.completed_at
            # Both must be tz-aware (UTC).
            assert result.started_at.tzinfo is not None
            assert result.completed_at.tzinfo is not None
        finally:
            adapter.close()

    def test_policy_name_defaults_to_none(self, tmp_path):
        """When the caller doesn't pass policy_name, it stays None."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["users"], policy, audit)
            assert result.policy_name is None
        finally:
            adapter.close()

    def test_policy_name_propagates(self, tmp_path):
        """policy_name passed to classify_tables appears on the ScanReport."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["users"], policy, audit, policy_name="example")
            assert result.policy_name == "example"
        finally:
            adapter.close()

    def test_target_summary_defaults_to_none(self, tmp_path):
        """When the caller doesn't pass target_summary, it stays None."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["users"], policy, audit)
            assert result.target_summary is None
        finally:
            adapter.close()

    def test_target_summary_propagates(self, tmp_path):
        """target_summary passed to classify_tables appears on the ScanReport."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            redacted = "sqlite:///" + str(tmp_path) + "/test.db"
            result = classify_tables(adapter, ["users"], policy, audit, target_summary=redacted)
            assert result.target_summary == redacted
        finally:
            adapter.close()


class TestClassifyTablesAuditEvents:
    """SCAN_STARTED and SCAN_COMPLETED bookend events."""

    def test_emits_scan_started_event(self, tmp_path):
        """A SCAN_STARTED event is recorded."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, storage = _make_audit_setup()
            classify_tables(adapter, ["users", "orders"], policy, audit)
            event_types = [e.event_type for e in storage.read_all()]
            assert EventType.SCAN_STARTED in event_types
        finally:
            adapter.close()

    def test_emits_scan_completed_event(self, tmp_path):
        """A SCAN_COMPLETED event is recorded."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, storage = _make_audit_setup()
            classify_tables(adapter, ["users", "orders"], policy, audit)
            event_types = [e.event_type for e in storage.read_all()]
            assert EventType.SCAN_COMPLETED in event_types
        finally:
            adapter.close()

    def test_scan_bookends_have_shared_scan_id(self, tmp_path):
        """SCAN_STARTED and SCAN_COMPLETED carry the same scan_id."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, storage = _make_audit_setup()
            classify_tables(adapter, ["users"], policy, audit)

            events = list(storage.read_all())
            started = next(e for e in events if e.event_type == EventType.SCAN_STARTED)
            completed = next(e for e in events if e.event_type == EventType.SCAN_COMPLETED)
            assert started.data["scan_id"] == completed.data["scan_id"]
        finally:
            adapter.close()

    def test_scan_started_records_table_count(self, tmp_path):
        """SCAN_STARTED data includes the input table count."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, storage = _make_audit_setup()
            classify_tables(adapter, ["users", "orders", "products"], policy, audit)
            events = list(storage.read_all())
            started = next(e for e in events if e.event_type == EventType.SCAN_STARTED)
            assert started.data["table_count"] == 3
        finally:
            adapter.close()

    def test_scan_completed_records_success_and_failure_counts(self, tmp_path):
        """SCAN_COMPLETED records success_count, failure_count, total_classifications."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, storage = _make_audit_setup()
            classify_tables(adapter, ["users", "orders"], policy, audit)

            events = list(storage.read_all())
            completed = next(e for e in events if e.event_type == EventType.SCAN_COMPLETED)
            assert completed.data["success_count"] == 2
            assert completed.data["failure_count"] == 0
            assert completed.data["total_classifications"] == 1  # email in users

        finally:
            adapter.close()

    def test_policy_name_propagates_when_provided(self, tmp_path):
        """If policy_name is provided, SCAN_STARTED carries it."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, storage = _make_audit_setup()
            classify_tables(adapter, ["users"], policy, audit, policy_name="example")
            events = list(storage.read_all())
            started = next(e for e in events if e.event_type == EventType.SCAN_STARTED)
            assert started.data["policy_name"] == "example"
        finally:
            adapter.close()

    def test_policy_name_omitted_when_not_provided(self, tmp_path):
        """If policy_name is not provided, SCAN_STARTED data has no such key."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, storage = _make_audit_setup()
            classify_tables(adapter, ["users"], policy, audit)
            events = list(storage.read_all())
            started = next(e for e in events if e.event_type == EventType.SCAN_STARTED)
            assert "policy_name" not in started.data
        finally:
            adapter.close()

    def test_default_actor_is_classify_tables(self, tmp_path):
        """Without explicit actor, events have actor='classify_tables'."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, storage = _make_audit_setup()
            classify_tables(adapter, ["users"], policy, audit)
            # The bookend events should have actor='classify_tables'.
            events = list(storage.read_all())
            scan_events = [
                e
                for e in events
                if e.event_type in (EventType.SCAN_STARTED, EventType.SCAN_COMPLETED)
            ]
            actors = {e.actor for e in scan_events}
            assert actors == {"classify_tables"}
        finally:
            adapter.close()


class TestClassifyTablesErrorHandling:
    """Per-table failures are isolated; the scan continues."""

    def test_missing_table_added_to_failed_tables(self, tmp_path):
        """A nonexistent table appears in failed_tables, not in tables."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["users", "ghost_table"], policy, audit)
            successful_names = {r.table for r in result.tables}
            failed_names = {f.name for f in result.failed_tables}
            assert "users" in successful_names
            assert "ghost_table" in failed_names
        finally:
            adapter.close()

    def test_scan_continues_after_failure(self, tmp_path):
        """A failing table in the middle does not stop subsequent tables."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(
                adapter,
                ["users", "ghost_table", "orders"],
                policy,
                audit,
            )
            successful_names = {r.table for r in result.tables}
            assert "users" in successful_names
            assert "orders" in successful_names
            assert len(result.failed_tables) == 1
        finally:
            adapter.close()

    def test_all_tables_fail_returns_empty_tables_list(self, tmp_path):
        """If every input table fails, tables is [] and failed_tables has them all."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["ghost1", "ghost2"], policy, audit)
            assert result.tables == []
            assert len(result.failed_tables) == 2
            assert {f.name for f in result.failed_tables} == {"ghost1", "ghost2"}
        finally:
            adapter.close()

    def test_failed_table_carries_error_message(self, tmp_path):
        """FailedTable.error is non-empty and human-readable."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            result = classify_tables(adapter, ["ghost_table"], policy, audit)
            assert len(result.failed_tables) == 1
            assert result.failed_tables[0].error  # non-empty string
        finally:
            adapter.close()

    def test_empty_tables_list_returns_empty_result(self, tmp_path):
        """An empty tables input produces an empty ScanReport and still emits bookends."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, storage = _make_audit_setup()
            result = classify_tables(adapter, [], policy, audit)
            assert result.tables == []
            assert result.failed_tables == []
            # Bookend events should still fire
            event_types = [e.event_type for e in storage.read_all()]
            assert EventType.SCAN_STARTED in event_types
            assert EventType.SCAN_COMPLETED in event_types
        finally:
            adapter.close()


class TestClassifyTablesCallbacks:
    """Optional progress callbacks fire at the right moments."""

    def test_on_table_start_invoked_for_each_table(self, tmp_path):
        """on_table_start receives the name of every input table."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            seen: list[str] = []
            classify_tables(
                adapter,
                ["users", "orders"],
                policy,
                audit,
                on_table_start=lambda name: seen.append(name),
            )
            assert seen == ["users", "orders"]
        finally:
            adapter.close()

    def test_on_table_complete_invoked_for_successful_tables(self, tmp_path):
        """on_table_complete fires with the report for each success."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            completed: list[tuple[str, int]] = []
            classify_tables(
                adapter,
                ["users", "orders"],
                policy,
                audit,
                on_table_complete=lambda name, report: completed.append(
                    (name, report.columns_attempted)
                ),
            )
            assert len(completed) == 2
            names = [c[0] for c in completed]
            assert set(names) == {"users", "orders"}
        finally:
            adapter.close()

    def test_on_table_failed_invoked_for_failed_tables(self, tmp_path):
        """on_table_failed fires for tables that couldn't be classified."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            failures: list[tuple[str, str]] = []
            classify_tables(
                adapter,
                ["users", "ghost_table"],
                policy,
                audit,
                on_table_failed=lambda name, err: failures.append((name, err)),
            )
            assert len(failures) == 1
            assert failures[0][0] == "ghost_table"
            assert failures[0][1]  # non-empty error string
        finally:
            adapter.close()

    def test_callbacks_default_to_none_no_op(self, tmp_path):
        """If no callbacks are passed, classify_tables runs cleanly."""
        dsn = _make_multi_table_db(tmp_path)
        adapter = SqliteAdapter()
        adapter.connect(dsn)
        try:
            policy = _make_policy([_dict_rule("email_cols", ["email"])])
            audit, _ = _make_audit_setup()
            # Just verify no error - we're testing that None callbacks
            # don't get invoked.
            result = classify_tables(adapter, ["users"], policy, audit)
            assert len(result.tables) == 1
        finally:
            adapter.close()


class TestScanReportShape:
    """Pydantic constraints on ScanReport and FailedTable."""

    def _empty_kwargs(self) -> dict:
        """Minimal valid kwargs for a no-tables ScanReport."""
        now = datetime.now(timezone.utc)
        return {
            "scan_id": "00000000-0000-0000-0000-000000000000",
            "started_at": now,
            "completed_at": now,
            "policy_name": None,
            "target_summary": None,
            "tables": [],
            "failed_tables": [],
        }

    def test_scan_report_is_frozen(self):
        """ScanReport is immutable after construction."""
        result = ScanReport(**self._empty_kwargs())
        with pytest.raises(ValidationError):
            result.tables = [None]  # type: ignore[misc]

    def test_scan_report_rejects_unknown_fields(self):
        """ScanReport enforces extra='forbid'."""
        kwargs = self._empty_kwargs()
        kwargs["extra_field"] = "x"
        with pytest.raises(ValidationError):
            ScanReport(**kwargs)

    def test_scan_report_requires_scan_id(self):
        """scan_id is a required field; ScanReport rejects construction without it."""
        kwargs = self._empty_kwargs()
        del kwargs["scan_id"]
        with pytest.raises(ValidationError):
            ScanReport(**kwargs)

    def test_scan_report_requires_started_at(self):
        """started_at is required."""
        kwargs = self._empty_kwargs()
        del kwargs["started_at"]
        with pytest.raises(ValidationError):
            ScanReport(**kwargs)

    def test_scan_report_requires_completed_at(self):
        """completed_at is required."""
        kwargs = self._empty_kwargs()
        del kwargs["completed_at"]
        with pytest.raises(ValidationError):
            ScanReport(**kwargs)

    def test_scan_report_policy_name_optional(self):
        """policy_name defaults to None when explicitly None."""
        report = ScanReport(**self._empty_kwargs())
        assert report.policy_name is None

    def test_scan_report_target_summary_optional(self):
        """target_summary defaults to None when explicitly None."""
        report = ScanReport(**self._empty_kwargs())
        assert report.target_summary is None

    def test_scan_report_accepts_metadata_strings(self):
        """policy_name and target_summary accept str values."""
        kwargs = self._empty_kwargs()
        kwargs["policy_name"] = "example"
        kwargs["target_summary"] = "postgresql://localhost:5432/db (password redacted)"
        report = ScanReport(**kwargs)
        assert report.policy_name == "example"
        assert report.target_summary == "postgresql://localhost:5432/db (password redacted)"

    def test_failed_table_is_frozen(self):
        """FailedTable is immutable."""
        ft = FailedTable(name="x", error="oops")
        with pytest.raises(ValidationError):
            ft.name = "y"  # type: ignore[misc]

    def test_failed_table_rejects_unknown_fields(self):
        """FailedTable enforces extra='forbid'."""
        with pytest.raises(ValidationError):
            FailedTable(name="x", error="oops", extra="bad")  # type: ignore[call-arg]
