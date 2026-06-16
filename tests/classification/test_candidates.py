"""Tests for the list_table_candidates function.

Pre-scan candidate discovery: given a policy and an adapter, walk
every table and count how many columns match the policy's name-based
rules. Used by the CLI's `dataprism table candidates` command to help
the user decide what to scan.

These tests verify:
- The happy path: returns a sorted list of TableCandidate
- Match counting: name-based rules contribute, value-based rules don't
- Sorting: match_count desc, then table name asc
- Schema parameter: forwarded to adapter.list_tables
- Error propagation: list_columns failures are NOT caught
- Pydantic shape: TableCandidate is frozen, rejects extra fields
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dataprism.adapters.errors import AdapterError
from dataprism.adapters.sqlite import SqliteAdapter
from dataprism.classification.candidates import (
    TableCandidate,
    list_table_candidates,
)
from dataprism.policy.models import (
    ClassificationLabel,
    ClassificationPolicy,
    DictionaryMatchMode,
    DictionaryRule,
    RegexRule,
    RegexTarget,
    StatisticalRule,
)

# ---- Test helpers ---------------------------------------------------
#
# Local copies of helpers also used in test_table.py and test_engine.py.
# Third file using these (the case for shared fixtures grows).
# Tracked in docs/ARCHITECTURE.md Section 8 "Test helper consolidation".


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


def _statistical_rule(
    name: str,
    pattern: str,
    classification: ClassificationLabel = ClassificationLabel.PII,
) -> StatisticalRule:
    return StatisticalRule(
        type="statistical",
        name=name,
        pattern=pattern,
        classification=classification,
    )


def _make_policy(rules: list) -> ClassificationPolicy:
    return ClassificationPolicy(version=1, classifiers=rules)


def _make_db_with_tables(tmp_path) -> str:
    """Create a SQLite database with three tables of varying shapes.

    Schema:
        users (id, email, name, active)             - 4 cols
        orders (id, customer_id, total)             - 3 cols
        products (id, sku, name, price)             - 4 cols
        zlogs (id, message)                          - 2 cols (alphabetically last)

    The 'zlogs' table is included to verify alphabetical tiebreaking.
    """
    from sqlalchemy import create_engine, text

    dsn = f"sqlite:///{tmp_path / 'candidates.db'}"
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("CREATE TABLE users (id INTEGER, email TEXT, name TEXT, active INTEGER)")
            )
            conn.execute(text("CREATE TABLE orders (id INTEGER, customer_id INTEGER, total REAL)"))
            conn.execute(
                text("CREATE TABLE products (id INTEGER, sku TEXT, name TEXT, price REAL)")
            )
            conn.execute(text("CREATE TABLE zlogs (id INTEGER, message TEXT)"))
    finally:
        engine.dispose()
    return dsn


def _connect(tmp_path) -> SqliteAdapter:
    dsn = _make_db_with_tables(tmp_path)
    adapter = SqliteAdapter()
    adapter.connect(dsn)
    return adapter


# =====================================================================
# Happy path
# =====================================================================


class TestListTableCandidatesHappyPath:
    """The function returns a list of TableCandidate."""

    def test_returns_list_of_table_candidate(self, tmp_path):
        """Result is a list, and each element is a TableCandidate."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy([_dict_rule("email", ["email"])])
            result = list_table_candidates(adapter, policy)
            assert isinstance(result, list)
            assert all(isinstance(c, TableCandidate) for c in result)
        finally:
            adapter.close()

    def test_includes_all_tables(self, tmp_path):
        """Every table in the DB shows up in the result, even with 0 matches."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy([_dict_rule("email", ["email"])])
            result = list_table_candidates(adapter, policy)
            names = {c.table for c in result}
            assert names == {"users", "orders", "products", "zlogs"}
        finally:
            adapter.close()

    def test_column_count_matches_actual_columns(self, tmp_path):
        """column_count equals the actual number of columns."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy([_dict_rule("email", ["email"])])
            result = list_table_candidates(adapter, policy)
            by_name = {c.table: c for c in result}
            assert by_name["users"].column_count == 4
            assert by_name["orders"].column_count == 3
            assert by_name["products"].column_count == 4
            assert by_name["zlogs"].column_count == 2
        finally:
            adapter.close()

    def test_schema_name_is_none_for_sqlite(self, tmp_path):
        """SQLite has no schema concept; schema_name is None for every table."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy([_dict_rule("email", ["email"])])
            result = list_table_candidates(adapter, policy)
            assert all(c.schema_name is None for c in result)
        finally:
            adapter.close()


# =====================================================================
# Match counting
# =====================================================================


class TestMatchCounting:
    """match_count reflects name-based rule matches only."""

    def test_dictionary_rule_matches_column_name(self, tmp_path):
        """A dictionary rule matching 'email' counts users.email."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy([_dict_rule("email_col", ["email"])])
            result = list_table_candidates(adapter, policy)
            by_name = {c.table: c for c in result}
            assert by_name["users"].match_count == 1
            assert by_name["orders"].match_count == 0
            assert by_name["zlogs"].match_count == 0
        finally:
            adapter.close()

    def test_regex_column_name_matches(self, tmp_path):
        """A regex with target=COLUMN_NAME matches columns by name."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy([_regex_rule("id_pattern", RegexTarget.COLUMN_NAME, r"_id$")])
            result = list_table_candidates(adapter, policy)
            by_name = {c.table: c for c in result}
            # orders has customer_id; nothing else ends in _id
            assert by_name["orders"].match_count == 1
            assert by_name["users"].match_count == 0
            assert by_name["products"].match_count == 0
        finally:
            adapter.close()

    def test_regex_column_value_does_not_contribute(self, tmp_path):
        """A regex with target=COLUMN_VALUE is skipped (would need sampling)."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy([_regex_rule("email_values", RegexTarget.COLUMN_VALUE, r"@")])
            result = list_table_candidates(adapter, policy)
            # No matches at all - the only rule was value-based and is skipped
            assert all(c.match_count == 0 for c in result)
        finally:
            adapter.close()

    def test_statistical_rule_does_not_contribute(self, tmp_path):
        """A statistical rule is skipped (would need sampling)."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy([_statistical_rule("emails", r"@")])
            result = list_table_candidates(adapter, policy)
            assert all(c.match_count == 0 for c in result)
        finally:
            adapter.close()

    def test_multiple_rules_match_same_column_count_once(self, tmp_path):
        """If two rules both match the same column, it counts ONCE."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy(
                [
                    _dict_rule("email_a", ["email"]),
                    _dict_rule("email_b", ["email"]),
                ]
            )
            result = list_table_candidates(adapter, policy)
            by_name = {c.table: c for c in result}
            # users.email matches both rules but the column only counts once
            assert by_name["users"].match_count == 1
        finally:
            adapter.close()

    def test_multiple_columns_in_same_table_each_count(self, tmp_path):
        """Two different columns each matching name rules contribute 2."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy(
                [
                    _dict_rule("email_col", ["email"]),
                    _dict_rule("name_col", ["name"]),
                ]
            )
            result = list_table_candidates(adapter, policy)
            by_name = {c.table: c for c in result}
            # users has both email and name
            assert by_name["users"].match_count == 2
        finally:
            adapter.close()

    def test_empty_policy_yields_zero_matches(self, tmp_path):
        """A policy with no rules at all yields 0 for everything."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy([])
            result = list_table_candidates(adapter, policy)
            assert all(c.match_count == 0 for c in result)
        finally:
            adapter.close()


# =====================================================================
# Sorting
# =====================================================================


class TestSorting:
    """Results sort by match_count desc, then table name asc."""

    def test_sorts_by_match_count_descending(self, tmp_path):
        """Tables with more matches come first."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy(
                [
                    _dict_rule("email_col", ["email"]),
                    _dict_rule("name_col", ["name"]),
                    _dict_rule("id_col", ["customer_id"]),
                ]
            )
            result = list_table_candidates(adapter, policy)
            # users: email, name -> 2 matches
            # products: name -> 1 match
            # orders: customer_id -> 1 match (alphabetically before products)
            # zlogs: 0 matches
            counts = [(c.table, c.match_count) for c in result]
            assert counts == [
                ("users", 2),
                ("orders", 1),
                ("products", 1),
                ("zlogs", 0),
            ]
        finally:
            adapter.close()

    def test_alphabetical_tiebreaker_for_zero_matches(self, tmp_path):
        """Zero-match tables sort alphabetically among themselves."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy([])  # all zero
            result = list_table_candidates(adapter, policy)
            names = [c.table for c in result]
            assert names == ["orders", "products", "users", "zlogs"]
        finally:
            adapter.close()


# =====================================================================
# Schema parameter
# =====================================================================


class TestSchemaParameter:
    """schema parameter is forwarded to adapter.list_tables."""

    def test_none_schema_uses_default_scope(self, tmp_path):
        """schema=None lists all SQLite tables (its default scope)."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy([_dict_rule("email", ["email"])])
            result = list_table_candidates(adapter, policy, schema=None)
            assert len(result) == 4
        finally:
            adapter.close()

    def test_explicit_schema_for_sqlite_returns_same_tables(self, tmp_path):
        """SQLite ignores the schema parameter; result is the same."""
        adapter = _connect(tmp_path)
        try:
            policy = _make_policy([_dict_rule("email", ["email"])])
            baseline = list_table_candidates(adapter, policy, schema=None)
            with_schema = list_table_candidates(adapter, policy, schema="ignored")
            assert {c.table for c in with_schema} == {c.table for c in baseline}
        finally:
            adapter.close()


# =====================================================================
# Error propagation
# =====================================================================


class TestErrorPropagation:
    """list_columns failures propagate to the caller."""

    def test_disconnected_adapter_raises(self, tmp_path):
        """Calling without connecting raises (propagated from list_tables)."""
        adapter = SqliteAdapter()  # not connected
        policy = _make_policy([_dict_rule("email", ["email"])])
        with pytest.raises(AdapterError):
            list_table_candidates(adapter, policy)


# =====================================================================
# Pydantic shape
# =====================================================================


class TestTableCandidateShape:
    """Pydantic constraints on TableCandidate."""

    def test_table_candidate_is_frozen(self):
        """TableCandidate is immutable after construction."""
        c = TableCandidate(table="users", schema_name=None, column_count=5, match_count=2)
        with pytest.raises(ValidationError):
            c.match_count = 99  # type: ignore[misc]

    def test_table_candidate_rejects_unknown_fields(self):
        """TableCandidate enforces extra='forbid'."""
        with pytest.raises(ValidationError):
            TableCandidate(  # type: ignore[call-arg]
                table="users",
                schema_name=None,
                column_count=5,
                match_count=2,
                extra="bad",
            )
