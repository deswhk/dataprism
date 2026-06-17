"""Output rendering for the CLI.

This module produces strings - either for stdout, or written by the
caller to a file. Four consumers:

1. Single-table classify text/JSON: render_text/render_json over a
   TableClassificationReport. Text goes to stdout; JSON goes to
   stdout or is piped further.

2. Multi-table classify progress: render_progress_start +
   render_progress_complete_continuation (or
   render_progress_error_continuation) emit a one-line-per-table
   progress feed. render_scan_summary emits the final summary.

3. Candidates listing: render_candidates_text /
   render_candidates_json over a list[TableCandidate].

4. HTML report: render_html_report over a ScanReport, written to
   disk by the caller (a self-contained governance artifact, one
   file per scan). Uses a Jinja2 template in cli/templates/.

The first three are short Python functions composing strings. The
HTML report is large enough to warrant a template engine; the rest
are not.
"""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from dataprism.classification.candidates import TableCandidate
from dataprism.classification.table import (
    ScanReport,
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


def render_scan_summary(scan_result: ScanReport) -> str:
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


# ---- HTML report (Jinja2) --------------------------------------------


# Module-level Jinja2 environment. Single FileSystemLoader pointing at
# the templates/ directory next to this file. Autoescape is on for
# HTML output - protects against unexpected HTML in DSN strings, error
# messages, etc. (Defense in depth; the engine doesn't pass
# user-controlled HTML in normal use.)
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "htm", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_html_report(
    scan_report: ScanReport,
    *,
    audit_log_path: Path | str | None = None,
) -> str:
    """Render a ScanReport as a self-contained HTML document.

    The output is a single HTML file with inline CSS, no JavaScript,
    no external assets. Designed for distribution as a governance
    artifact: open, share, print.

    Sensitive data is NOT included. The renderer relies on
    ScanReport's structure (column names, classification labels,
    rule names, rule types - no sampled values).

    Args:
        scan_report: The scan to render.
        audit_log_path: Optional path to the audit log, shown in the
            report's footer for cross-reference. The CLI passes it;
            programmatic callers may omit.

    Returns:
        A complete HTML document as a string. Caller writes it to
        disk.
    """
    # Compute derived stats that the template uses.
    duration = (scan_report.completed_at - scan_report.started_at).total_seconds()
    duration_seconds = f"{duration:.2f}"

    total_columns = sum(t.columns_attempted for t in scan_report.tables)
    total_classified_columns = sum(
        sum(1 for matches in t.matches_by_column.values() if matches) for t in scan_report.tables
    )

    # Build a sorted (category, count) list. A column counts once per
    # distinct category it has - if a column matched two PII rules, that
    # still counts as one PII column.
    category_counts: dict[str, int] = {}
    for table in scan_report.tables:
        for matches in table.matches_by_column.values():
            distinct_categories = {m.classification for m in matches}
            for cat in distinct_categories:
                category_counts[cat] = category_counts.get(cat, 0) + 1
    classifications_by_category = sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0]))

    # Detect whether any table has per-column errors (the template
    # uses this to decide whether to show the Errors section header).
    any_column_errors = any(t.errors for t in scan_report.tables)

    template = _jinja_env.get_template("report.html.j2")
    return template.render(
        scan_report=scan_report,
        duration_seconds=duration_seconds,
        total_columns=total_columns,
        total_classified_columns=total_classified_columns,
        classifications_by_category=classifications_by_category,
        any_column_errors=any_column_errors,
        audit_log_path=str(audit_log_path) if audit_log_path else None,
    )
