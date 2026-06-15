"""Output rendering for the CLI.

Two formats: text (default, human-readable) and JSON (--output json).

Both consume the same TableClassificationReport. The text renderer
builds a friendly summary; the JSON renderer delegates to Pydantic's
model_dump_json() for canonical serialization.

Future formats (HTML, CSV, etc.) would add new render functions here.
If we ever need many formats or per-format customization, switching
to Jinja2 templates would be a natural evolution (see Section 8 of
docs/ARCHITECTURE.md).
"""

from __future__ import annotations

from dataprism.classification.table import TableClassificationReport


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
