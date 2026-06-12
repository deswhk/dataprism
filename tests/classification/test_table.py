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
    TableClassificationReport,
    classify_table,
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
