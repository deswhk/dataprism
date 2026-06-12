"""High-level classify_table API.

Combines a DatabaseAdapter, a ClassificationPolicy, and an AuditService
into a single function that classifies every column in a table. This is
the function callers reach for when they want "classify this table"
without writing the per-column iteration boilerplate themselves.

The function:
- Takes an already-connected adapter (caller manages connection lifecycle)
- Emits TABLE_CLASSIFICATION_STARTED at start of run
- Iterates columns, sampling and classifying each
- Records per-column errors without aborting the whole run
- Emits TABLE_CLASSIFICATION_COMPLETED at end
- Returns a TableClassificationReport with matches and errors

For per-column custom sampling or filtering, callers should use the
lower-level adapter + engine APIs directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from dataprism.adapters.errors import AdapterError
from dataprism.adapters.protocol import DatabaseAdapter, SamplingStrategy
from dataprism.audit.events import EventType
from dataprism.audit.service import AuditService
from dataprism.classification.engine import ClassificationEngine
from dataprism.classification.results import ClassificationResult
from dataprism.policy.models import ClassificationPolicy


@dataclass(frozen=True)
class ColumnError:
    """Records a per-column failure during table classification.

    Carries the column name and a human-readable error description.
    The original exception is not preserved (we don't want to leak
    SQLAlchemy internals or potentially sensitive details from the
    error message); use logs for debugging if needed.
    """

    column_name: str
    error: str


class TableClassificationReport(BaseModel):
    """The result of classify_table.

    Attributes:
        table: Name of the table that was classified.
        columns_attempted: Total number of columns the function tried
            to classify. Equals the sum of successful + failed columns.
        matches_by_column: For each successfully-classified column, a
            list of ClassificationResult instances (one per matching
            rule). Empty list if a column had no matches. Columns that
            failed are NOT in this dict.
        errors: List of ColumnError records for columns that failed.
            Empty if all columns succeeded.

    Consistency invariant:
        len(matches_by_column) + len(errors) == columns_attempted

    The report shape lets callers ask three useful questions:
    - "Was the run fully successful?" -> len(errors) == 0
    - "How many columns matched at all?" -> count non-empty lists
    - "What columns failed and why?" -> iterate errors
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str
    columns_attempted: int
    matches_by_column: dict[str, list[ClassificationResult]]
    errors: list[ColumnError]


def classify_table(
    adapter: DatabaseAdapter,
    table: str,
    policy: ClassificationPolicy,
    audit: AuditService,
    *,
    sample_size: int = 1000,
    strategy: SamplingStrategy = SamplingStrategy.SEQUENTIAL,
    actor: str = "classify_table",
) -> TableClassificationReport:
    """Classify every column in a table.

    Args:
        adapter: A connected DatabaseAdapter. Caller is responsible for
            calling connect() before and close() after.
        table: Name of the table to classify. May be 'tablename' or
            'schema.tablename' (depending on adapter support).
        policy: The classification policy to apply to each column.
        audit: AuditService for recording events.
        sample_size: Maximum number of values to sample per column.
            Default 1000. Same value applied to every column.
        strategy: How to sample values. Default SEQUENTIAL (fast,
            deterministic). Use RANDOM for statistical rigor.
        actor: Actor name recorded on audit events. Default
            "classify_table".

    Returns:
        A TableClassificationReport with per-column matches and any
        per-column errors.

    Raises:
        AdapterError: If list_columns() itself fails (e.g., table
            doesn't exist or adapter isn't connected). Per-column
            sampling/classification failures are caught and recorded
            in the report's errors list, not raised.
    """
    # Discover columns. If this fails, we have no per-column work to
    # do - propagate the error to the caller rather than swallow it.
    columns = adapter.list_columns(table)
    columns_count = len(columns)

    # Record the start event before doing any work
    audit.record(
        event_type=EventType.TABLE_CLASSIFICATION_STARTED,
        actor=actor,
        data={"table": table, "columns_count": columns_count},
    )

    # Construct the engine once for this table classification run
    engine = ClassificationEngine(policy, audit, actor=actor)

    matches_by_column: dict[str, list[ClassificationResult]] = {}
    errors: list[ColumnError] = []

    for column in columns:
        try:
            samples = adapter.sample_values(
                table,
                column.name,
                n=sample_size,
                strategy=strategy,
            )
            column_results = engine.classify(column.name, samples.text)
            matches_by_column[column.name] = column_results
        except AdapterError as e:
            # Record the failure in the audit log
            audit.record(
                event_type=EventType.CLASSIFICATION_FAILED,
                actor=actor,
                data={
                    "table": table,
                    "column_name": column.name,
                    "error": str(e),
                },
            )
            errors.append(ColumnError(column_name=column.name, error=str(e)))

    # Record the completion event after all columns processed
    audit.record(
        event_type=EventType.TABLE_CLASSIFICATION_COMPLETED,
        actor=actor,
        data={
            "table": table,
            "columns_succeeded": len(matches_by_column),
            "columns_failed": len(errors),
        },
    )

    return TableClassificationReport(
        table=table,
        columns_attempted=columns_count,
        matches_by_column=matches_by_column,
        errors=errors,
    )
