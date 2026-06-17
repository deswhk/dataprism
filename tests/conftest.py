"""Project-wide pytest fixtures for dataprism tests.

Each fixture defined here is automatically available to every test in
the tests/ tree, including subpackages, without explicit import.
Pytest finds conftest.py at every level of the test tree and exposes
its fixtures to descendants.

The fixtures here are *factory-style*: each one yields a callable that
the test calls with its own arguments to produce a fresh artifact.
This pattern lets one fixture serve many tests with different inputs
(different table names, different rule names, different paths) while
still keeping setup code in one place.

Local helpers that aren't shared across subpackages stay in the test
module that uses them (the _strip_ansi helper in test_main.py, the
report-builder helpers in test_render.py, the audit-event factory in
test_storage.py, etc.).

If you find yourself adding the same factory in two test files,
consider moving it here instead.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from dataprism.audit.service import AuditService
from dataprism.audit.storage import InMemoryStorage
from dataprism.classification.engine import ClassificationEngine
from dataprism.policy.models import (
    ClassificationLabel,
    ClassificationPolicy,
    DictionaryMatchMode,
    DictionaryRule,
    RegexRule,
    RegexTarget,
    StatisticalRule,
)

# ---- Rule factories --------------------------------------------------


@pytest.fixture
def make_dict_rule():
    """Factory for DictionaryRule (default classification=PII)."""

    def _make(
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

    return _make


@pytest.fixture
def make_regex_rule():
    """Factory for RegexRule (default classification=PII)."""

    def _make(
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

    return _make


@pytest.fixture
def make_statistical_rule():
    """Factory for StatisticalRule (default classification=PII)."""

    def _make(
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

    return _make


# ---- Policy and engine factories -------------------------------------


@pytest.fixture
def make_policy():
    """Factory for ClassificationPolicy (version=1, given rules)."""

    def _make(rules: list) -> ClassificationPolicy:
        return ClassificationPolicy(version=1, classifiers=rules)

    return _make


@pytest.fixture
def make_audit_setup():
    """Factory: (AuditService, InMemoryStorage) with a configurable actor.

    Tests inspect the storage to verify audit events were recorded.
    The actor parameter controls what name is recorded on events; it
    defaults to a generic value, but tests asserting on actor can
    pass their own.
    """

    def _make(actor: str = "test") -> tuple[AuditService, InMemoryStorage]:
        storage = InMemoryStorage()
        audit = AuditService(storage)
        return audit, storage

    return _make


@pytest.fixture
def make_engine(make_policy, make_audit_setup):
    """Factory for ClassificationEngine pre-wired with an in-memory audit.

    Returns (engine, storage) so tests can inspect both the engine's
    behavior and the audit events it records. Builds the policy from
    the given rule list; uses the default actor or a passed one.

    Depends on the other factories so changes propagate consistently.
    """

    def _make(rules: list, actor: str = "classification_engine"):
        policy = make_policy(rules)
        audit, storage = make_audit_setup(actor=actor)
        engine = ClassificationEngine(policy, audit, actor=actor)
        return engine, storage

    return _make


# ---- Database fixtures -----------------------------------------------


@pytest.fixture
def make_users_db_dsn():
    """Factory for a single-table SQLite users database.

    Schema (the rich variant - also used by tests/adapters/fixtures.py):
        users (id INTEGER, email TEXT, name TEXT, active INTEGER, score REAL)
        5 rows including one NULL name and one NULL score, for testing
        null-handling in samplers.

    Returns the DSN string. Caller passes the desired file path
    (typically tmp_path / "test.db").
    """

    def _make(path) -> str:
        dsn = f"sqlite:///{path}"
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
                conn.execute(
                    text("INSERT INTO users VALUES (5, 'eve@example.com', 'Eve', 0, 50.5)")
                )
        finally:
            engine.dispose()
        return dsn

    return _make


@pytest.fixture
def make_multi_table_db_dsn():
    """Factory for a multi-table SQLite database (users, orders, products).

    Schema:
        users (id, email, name, active, score)    5 rows, no NULLs
        orders (id, customer_id, total)           3 rows
        products (id, sku, name, price)           3 rows

    The users schema matches make_users_db_dsn so tests using either
    fixture get consistent column counts and types. Returns the DSN.
    """

    def _make(path) -> str:
        dsn = f"sqlite:///{path}"
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
                    text("INSERT INTO users VALUES (1, 'a@example.com', 'Alice', 1, 99.5)")
                )
                conn.execute(text("INSERT INTO users VALUES (2, 'b@example.com', 'Bob', 1, 88.0)"))
                conn.execute(
                    text("INSERT INTO users VALUES (3, 'c@example.com', 'Carol', 1, 77.5)")
                )
                conn.execute(text("INSERT INTO users VALUES (4, 'd@example.com', 'Dan', 0, 66.0)"))
                conn.execute(text("INSERT INTO users VALUES (5, 'e@example.com', 'Eve', 1, 55.5)"))

                conn.execute(
                    text("CREATE TABLE orders (id INTEGER, customer_id INTEGER, total REAL)")
                )
                conn.execute(text("INSERT INTO orders VALUES (1, 1, 49.99)"))
                conn.execute(text("INSERT INTO orders VALUES (2, 2, 19.50)"))
                conn.execute(text("INSERT INTO orders VALUES (3, 3, 75.00)"))

                conn.execute(
                    text("CREATE TABLE products (id INTEGER, sku TEXT, name TEXT, price REAL)")
                )
                conn.execute(text("INSERT INTO products VALUES (1, 'A-001', 'Widget', 9.99)"))
                conn.execute(text("INSERT INTO products VALUES (2, 'A-002', 'Gadget', 19.99)"))
                conn.execute(text("INSERT INTO products VALUES (3, 'A-003', 'Sprocket', 4.49)"))
        finally:
            engine.dispose()
        return dsn

    return _make
