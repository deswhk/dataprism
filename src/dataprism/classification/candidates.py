"""Table candidates discovery.

Given a policy and a connected adapter, this module walks every table
in scope and reports how many columns in each table match the policy's
NAME-BASED rules (regex with target=column_name; dictionary).

Statistical rules and value-target regex rules are NOT evaluated here
- they require sampling data, which would defeat the purpose of a
cheap pre-scan. The output is a HEURISTIC: a table with 0 matches
may still contain sensitive data in oddly-named columns; classify to
be sure.

Results are sorted by match_count desc, then table name asc, so the
user sees the most likely scan targets first.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from dataprism.adapters.protocol import DatabaseAdapter
from dataprism.classification.evaluators import evaluate
from dataprism.policy.models import (
    ClassificationPolicy,
    ClassificationRule,
    DictionaryRule,
    RegexRule,
    RegexTarget,
)


class TableCandidate(BaseModel):
    """A table considered for classification, annotated with metadata.

    Returned by list_table_candidates() as part of a sorted list.

    Attributes:
        table: Table name (as reported by the adapter).
        schema_name: Schema the table belongs to. None for SQLite
            (which has no schema concept). May be 'public' or similar
            for Postgres.
        column_count: Total columns in this table.
        match_count: How many columns matched at least one name-based
            rule in the policy.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str
    schema_name: str | None
    column_count: int
    match_count: int


def _is_name_based(rule: ClassificationRule) -> bool:
    """Return True if this rule evaluates against the column name only.

    Name-based rules:
        - DictionaryRule (always inspects column name)
        - RegexRule with target=COLUMN_NAME

    Value-based rules (RegexRule with target=COLUMN_VALUE,
    StatisticalRule) are excluded because they require sampling
    data, which list_table_candidates intentionally avoids.
    """
    if isinstance(rule, DictionaryRule):
        return True
    if isinstance(rule, RegexRule) and rule.target == RegexTarget.COLUMN_NAME:
        return True
    return False


def list_table_candidates(
    adapter: DatabaseAdapter,
    policy: ClassificationPolicy,
    schema: str | None = None,
) -> list[TableCandidate]:
    """List candidate tables for classification, annotated with matches.

    For every table in the adapter's listed scope, count how many
    columns have a name that matches at least one of the policy's
    name-based rules. Returns the list sorted by match_count desc,
    then table name asc.

    The output is a heuristic: tables with 0 matches may still
    contain sensitive data (in oddly-named columns). It's a tool to
    help the user prioritize, not a substitute for classify.

    Args:
        adapter: A connected DatabaseAdapter.
        policy: The classification policy.
        schema: Optional schema name. None means "the adapter's
            default scope" (public for Postgres, all tables for
            SQLite).

    Returns:
        A list of TableCandidate, sorted by match_count desc, then
        name asc.

    Raises:
        AdapterError: If the adapter can't list tables or columns.
            (A list_columns failure on a single table is NOT caught
            here - we propagate so the user can see the problem.)
    """
    name_rules = [r for r in policy.classifiers if _is_name_based(r)]
    tables = adapter.list_tables(schema=schema)

    candidates: list[TableCandidate] = []
    for table in tables:
        columns = adapter.list_columns(table.name)
        match_count = 0
        for column in columns:
            for rule in name_rules:
                if evaluate(rule, column.name, []):
                    match_count += 1
                    break  # at most one match per column counts
        candidates.append(
            TableCandidate(
                table=table.name,
                schema_name=table.schema_name,
                column_count=len(columns),
                match_count=match_count,
            )
        )

    # Sort: match_count desc, then table name asc
    candidates.sort(key=lambda c: (-c.match_count, c.table))
    return candidates
