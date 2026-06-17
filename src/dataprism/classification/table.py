"""High-level classification APIs.

This module exposes two table-level classification functions:

- classify_table: classify every column in ONE table. Returns a
  TableClassificationReport (the per-table primitive).

- classify_tables: classify every column across one or more tables,
  with per-table failure isolation and scan-level metadata. Returns
  a ScanReport containing the per-table reports, any per-table
  failures, plus metadata (scan_id, started_at/completed_at,
  policy_name, target_summary) suitable for rendering as a
  governance artifact (HTML report).

Both combine a DatabaseAdapter, a ClassificationPolicy, and an
AuditService into a single function call. Lower-level uses (custom
sampling, partial column iteration) should use the adapter + engine
APIs directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

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


class FailedTable(BaseModel):
    """Records that an entire table could not be classified.

    Distinct from ColumnError, which records per-column failures
    inside a TableClassificationReport. A FailedTable means
    classify_table itself raised - typically because the table
    doesn't exist, permission was denied, or the adapter couldn't
    list its columns.

    Attributes:
        name: Name of the table that failed (as the user specified it).
        error: Human-readable description of what went wrong.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    error: str


class ScanReport(BaseModel):
    """The result of classify_tables.

    Aggregates per-table results, per-table failures, and scan-level
    metadata from a classification scan. The metadata fields make a
    ScanReport a self-contained governance artifact: it can be
    rendered (e.g., as HTML) without re-querying the adapter or
    re-reading the audit log.

    Attributes:
        scan_id: UUID for this scan. Matches the scan_id recorded on
            SCAN_STARTED / SCAN_COMPLETED audit events, so a rendered
            report can be cross-referenced against the audit trail.
        started_at: UTC timestamp when the scan began (just before
            SCAN_STARTED was recorded).
        completed_at: UTC timestamp when the scan finished (just before
            SCAN_COMPLETED was recorded).
        policy_name: Name of the policy used (e.g., "example"). Optional
            because the policy model itself doesn't carry its filename.
        target_summary: Human-readable description of the database
            target (e.g., a DSN with the password redacted). Optional
            for the same reason: the caller knows the DSN, not the
            engine. Callers passing it are responsible for redaction.
        tables: List of TableClassificationReport, one per
            successfully-classified table. Empty if all tables failed.
        failed_tables: List of FailedTable records, one per table
            that could not be classified at all. Empty if all
            tables succeeded.

    Consistency invariant:
        len(tables) + len(failed_tables) == number of unique input tables

    Note that per-column failures within a successfully-classified
    table appear in that table's TableClassificationReport.errors,
    not here. failed_tables is for whole-table failures only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scan_id: str
    started_at: datetime
    completed_at: datetime
    policy_name: str | None
    target_summary: str | None
    tables: list[TableClassificationReport]
    failed_tables: list[FailedTable]


def classify_tables(
    adapter: DatabaseAdapter,
    tables: list[str],
    policy: ClassificationPolicy,
    audit: AuditService,
    *,
    policy_name: str | None = None,
    target_summary: str | None = None,
    sample_size: int = 1000,
    strategy: SamplingStrategy = SamplingStrategy.SEQUENTIAL,
    actor: str = "classify_tables",
    on_table_start: Callable[[str], None] | None = None,
    on_table_complete: Callable[[str, TableClassificationReport], None] | None = None,
    on_table_failed: Callable[[str, str], None] | None = None,
) -> ScanReport:
    """Classify every column across multiple tables.

    Iterates over the given table names, calling classify_table for
    each. Per-table failures are caught and collected in the
    returned ScanResult's failed_tables list; the scan continues
    with the next table. The audit trail includes SCAN_STARTED and
    SCAN_COMPLETED bookend events with a shared scan_id, plus the
    per-table events emitted by classify_table.

    Args:
        adapter: A connected DatabaseAdapter.
        tables: List of table names to classify. Duplicates are NOT
            deduped here - callers must dedupe before passing.
        policy: The classification policy to apply.
        audit: AuditService for recording events.
        policy_name: Optional name of the policy (e.g. "example"),
            recorded on SCAN_STARTED for audit traceability. The
            policy model itself doesn't carry its filename; the
            caller (typically the CLI) knows it.
        target_summary: Optional human-readable description of the
            database target (e.g., DSN with password redacted).
            Echoed into the returned ScanReport for inclusion in
            governance artifacts. Callers passing it are responsible
            for redacting any secrets.
        sample_size: Maximum number of values to sample per column.
        strategy: How to sample values. Default SEQUENTIAL.
        actor: Actor name recorded on audit events. Default
            "classify_tables".
        on_table_start: Optional callback invoked before each table's
            classification begins. Receives the table name. Use for
            progress UI; do not raise from this callback (the engine
            does not catch).
        on_table_complete: Optional callback invoked after a table
            is successfully classified. Receives the table name and
            its TableClassificationReport.
        on_table_failed: Optional callback invoked when a table's
            classification fails (i.e., classify_table raised
            AdapterError). Receives the table name and the error
            message string.

    Returns:
        A ScanReport with per-table reports, per-table failures,
        and scan-level metadata.

    Raises:
        Does not raise for per-table failures (those go in
        failed_tables). Adapter errors specific to one table are
        caught; other exceptions propagate.
    """
    scan_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    table_count = len(tables)

    # SCAN_STARTED bookend. policy_name only included if provided.
    start_data: dict[str, object] = {
        "scan_id": scan_id,
        "table_count": table_count,
    }
    if policy_name is not None:
        start_data["policy_name"] = policy_name
    audit.record(
        event_type=EventType.SCAN_STARTED,
        actor=actor,
        data=start_data,
    )

    successful_reports: list[TableClassificationReport] = []
    failed: list[FailedTable] = []

    for table in tables:
        if on_table_start is not None:
            on_table_start(table)
        try:
            report = classify_table(
                adapter,
                table,
                policy,
                audit,
                sample_size=sample_size,
                strategy=strategy,
                actor=actor,
            )
            successful_reports.append(report)
            if on_table_complete is not None:
                on_table_complete(table, report)
        except AdapterError as e:
            failed.append(FailedTable(name=table, error=str(e)))
            if on_table_failed is not None:
                on_table_failed(table, str(e))

    # Total classifications = sum of columns with at least one match,
    # across all successful tables. A column contributes 1 if it had
    # any matching rule, 0 otherwise.
    total_classifications = sum(
        sum(1 for matches in report.matches_by_column.values() if matches)
        for report in successful_reports
    )

    completed_at = datetime.now(timezone.utc)

    # SCAN_COMPLETED bookend.
    audit.record(
        event_type=EventType.SCAN_COMPLETED,
        actor=actor,
        data={
            "scan_id": scan_id,
            "table_count": table_count,
            "success_count": len(successful_reports),
            "failure_count": len(failed),
            "total_classifications": total_classifications,
        },
    )

    return ScanReport(
        scan_id=scan_id,
        started_at=started_at,
        completed_at=completed_at,
        policy_name=policy_name,
        target_summary=target_summary,
        tables=successful_reports,
        failed_tables=failed,
    )
