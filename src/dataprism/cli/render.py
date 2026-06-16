"""Output rendering for the CLI.

This module produces strings for stdout. Two consumers:

1. Single-table classify: render_text/render_json over a
   TableClassificationReport. The existing PR 9/10 path.

2. Multi-table classify: render_progress_start +
   render_progress_complete_continuation (or
   render_progress_error_continuation) emit a one-line-per-table
   progress feed. render_scan_summary emits the final summary.

3. Candidates listing: render_candidates_text /
   render_candidates_json over a list[TableCandidate].

Future formats (HTML reports in PR 12) will likely use Jinja2
templates rather than Python string composition. See Section 8 of
docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import json

from dataprism.classification.candidates import TableCandidate
from dataprism.classification.table import (
    ScanResult,
    TableClassificationReport,
)


def render_text(report: TableClassificationReport) -> str:
    """Render a TableClassificationReport as human-readable text.

    Format:
        Classified table '<table>' (<N> columns[: <succ> succeeded, <fail> failed])

          <col1>          <CLASSIFICATION>  (matched: <rule1>, <rule2>)
          <col2>          (no matches)
          ...

        [Errors:
          <col_err1>      <error message>
          ...]

    The errors section is omitted entirely if there are no errors.

    Args:
        report: A TableClassificationReport from classify_table.

    Returns:
        A multi-line string ready to print to stdout.
    """
    lines: list[str] = []

    # Header
    n_attempted = report.columns_attempted
    n_failed = len(report.errors)
    n_succeeded = n_attempted - n_failed
    if n_failed > 0:
        header = (
            f"Classified table '{report.table}' "
            f"({n_attempted} columns: {n_succeeded} succeeded, {n_failed} failed)"
        )
    else:
        header = f"Classified table '{report.table}' ({n_attempted} columns)"
    lines.append(header)
    lines.append("")

    # Per-column results
    if report.matches_by_column:
        # Compute the column-name column width for alignment
        max_col_width = max(len(col) for col in report.matches_by_column)
        # Cap it to keep things readable for very long column names
        col_width = min(max_col_width, 30)

        for col, matches in report.matches_by_column.items():
            col_padded = col.ljust(col_width)
            if matches:
                # Group by classification label; list rule names
                classifications = sorted({match.classification for match in matches})
                rule_names = sorted(match.rule_name for match in matches)
                class_str = ", ".join(classifications)
                rules_str = ", ".join(rule_names)
                lines.append(f"  {col_padded}  {class_str}  (matched: {rules_str})")
            else:
                lines.append(f"  {col_padded}  (no matches)")
        lines.append("")

    # Errors section, if any
    if report.errors:
        lines.append("Errors:")
        # Width for error column names, capped
        max_err_width = max(len(e.column_name) for e in report.errors)
        err_width = min(max_err_width, 30)
        for err in report.errors:
            col_padded = err.column_name.ljust(err_width)
            lines.append(f"  {col_padded}  {err.error}")
        lines.append("")

    return "\n".join(lines)


def render_json(report: TableClassificationReport) -> str:
    """Render a TableClassificationReport as indented JSON.

    Uses Pydantic's model_dump_json() so the structure matches the
    Pydantic model exactly. This is the canonical JSON representation
    of the report and is suitable for piping to jq, parsing in other
    languages, or persisting.

    Args:
        report: A TableClassificationReport from classify_table.

    Returns:
        A JSON string with 2-space indentation.
    """
    return report.model_dump_json(indent=2)


# =====================================================================
# Multi-table classify - progress and summary
# =====================================================================


def render_progress_start(table: str) -> str:
    """Beginning of a per-table progress line.

    Emitted via typer.echo(..., nl=False) so the rest of the line can
    be appended by render_progress_complete_continuation or
    render_progress_error_continuation.

    Format: "Scanning <table>... "
    """
    return f"Scanning {table}... "


def render_progress_complete_continuation(
    report: TableClassificationReport,
) -> str:
    """End of a successful per-table progress line.

    Format: "<N> columns, <M> classifications"

    The 'classifications' count is columns with at least one matching
    rule (so a column matched by two rules counts once).
    """
    classifications = sum(1 for matches in report.matches_by_column.values() if matches)
    return f"{report.columns_attempted} columns, {classifications} classifications"


def render_progress_error_continuation(error: str) -> str:
    """End of a failed per-table progress line.

    Format: "ERROR (<reason>)"
    """
    return f"ERROR ({error})"


def render_scan_summary(scan_result: ScanResult) -> str:
    """Final summary line(s) for multi-table classify.

    Format (clean run):
        Scanned <N> tables. <M> classifications total.

    Format (with failures):
        Scanned <N> tables (<X> succeeded, <Y> failed). <M> classifications total.

    The total_classifications count sums per-table classification
    counts (a column with at least one matching rule contributes 1).
    """
    n_total = len(scan_result.tables) + len(scan_result.failed_tables)
    n_success = len(scan_result.tables)
    n_failed = len(scan_result.failed_tables)
    classifications = sum(
        sum(1 for matches in r.matches_by_column.values() if matches) for r in scan_result.tables
    )

    if n_failed > 0:
        return (
            f"Scanned {n_total} tables "
            f"({n_success} succeeded, {n_failed} failed). "
            f"{classifications} classifications total."
        )
    return f"Scanned {n_total} tables. {classifications} classifications total."


# =====================================================================
# Candidates - text and JSON
# =====================================================================


# Caveat shown in the text output of table candidates. The pre-scan
# evaluates name-based rules only (no data sampling), so absence of
# matches is a HINT, not a certificate of cleanliness.
_CANDIDATES_CAVEAT = (
    "Match counts reflect column-name rules only. Tables with 0 "
    "matching columns may still contain sensitive data; classify "
    "to be sure."
)


def render_candidates_text(
    candidates: list[TableCandidate],
    schema: str | None = None,
) -> str:
    """Render candidate tables as aligned text.

    Format:
        Tables in '<schema>' (<N>):

          <table1>          <C> cols,  <M> matching
          <table2>          <C> cols,  <M> matching
          ...

        <CAVEAT>

    If schema is None (no specific schema), the header reads
    "Tables in database (<N>):" instead.

    Args:
        candidates: A list of TableCandidate, already sorted by the
            engine (match_count desc, then name asc).
        schema: The schema name passed to list_table_candidates, or
            None for adapter default. Affects only the header text.

    Returns:
        A multi-line string ready for stdout.
    """
    lines: list[str] = []

    # Header
    if schema is not None:
        header = f"Tables in '{schema}' ({len(candidates)}):"
    else:
        header = f"Tables in database ({len(candidates)}):"
    lines.append(header)
    lines.append("")

    if candidates:
        # Column width for table names, capped for readability
        max_name = max(len(c.table) for c in candidates)
        name_width = min(max_name, 30)

        # Column width for the column-count number (so they align)
        max_cols = max(c.column_count for c in candidates)
        cols_width = len(str(max_cols))

        for c in candidates:
            name_padded = c.table.ljust(name_width)
            cols_padded = str(c.column_count).rjust(cols_width)
            lines.append(f"  {name_padded}  {cols_padded} cols, {c.match_count} matching")
        lines.append("")

    lines.append(_CANDIDATES_CAVEAT)
    return "\n".join(lines)


def render_candidates_json(
    candidates: list[TableCandidate],
    schema: str | None = None,
) -> str:
    """Render candidate tables as JSON.

    Structure:
        {
          "schema": "<schema or null>",
          "total_tables": <N>,
          "tables": [
            {"table": "...", "schema_name": "...", "column_count": N, "match_count": M},
            ...
          ]
        }

    The 'tables' list preserves the engine's sort order
    (match_count desc, then name asc).

    Args:
        candidates: A list of TableCandidate from list_table_candidates.
        schema: The schema parameter passed by the caller (echoed
            back at top level for scripting convenience).

    Returns:
        A JSON string with 2-space indentation.
    """
    payload = {
        "schema": schema,
        "total_tables": len(candidates),
        "tables": [c.model_dump() for c in candidates],
    }
    return json.dumps(payload, indent=2)
