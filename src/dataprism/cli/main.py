"""CLI entry point for dataprism.

Top-level command structure:
    dataprism table classify ...
    dataprism audit verify

Each subcommand reads what it needs from CLI flags and the
DATAPRISM_DSN environment variable. Audit logs live at a fixed
location (<project-root>/audit/audit.jsonl); the policy is named
and resolved against <project-root>/config/policies/<name>.yaml.

This module wires together the building blocks:
    - cli.paths   : project root, audit log path, policy path resolution
    - cli.adapters: DSN normalization and adapter selection
    - cli.render  : text and JSON rendering of reports
    - dataprism.classification.table : the classify_table function
    - dataprism.audit.* : audit service and storage
    - dataprism.policy.loader : policy loading
"""

from __future__ import annotations

import json
import os
from enum import StrEnum

import typer

from dataprism.adapters.errors import AdapterError
from dataprism.audit.service import AuditService
from dataprism.audit.storage import ChainVerificationError, JsonLinesStorage
from dataprism.classification.candidates import list_table_candidates
from dataprism.classification.table import classify_tables
from dataprism.cli import adapters as cli_adapters
from dataprism.cli import paths as cli_paths
from dataprism.cli.render import (
    render_candidates_json,
    render_candidates_text,
    render_html_report,
    render_json,
    render_progress_complete_continuation,
    render_progress_error_continuation,
    render_progress_start,
    render_scan_summary,
    render_text,
)
from dataprism.policy.loader import load_classification_policy

# ---- App scaffolding ------------------------------------------------

app = typer.Typer(
    help="dataprism: a data governance toolkit.",
    no_args_is_help=True,
)

table_app = typer.Typer(
    help="Commands that operate on database tables.",
    no_args_is_help=True,
)
app.add_typer(table_app, name="table")

audit_app = typer.Typer(
    help="Commands for inspecting the audit log.",
    no_args_is_help=True,
)
app.add_typer(audit_app, name="audit")


# ---- Output format enum (typer auto-generates choices) ---------------


class OutputFormat(StrEnum):
    """Supported output formats."""

    text = "text"
    json = "json"


# ---- Helpers ---------------------------------------------------------


def _read_dsn_from_env() -> str:
    """Read DATAPRISM_DSN from the environment.

    Exits with a clear error message if the env var is not set.
    """
    dsn = os.environ.get("DATAPRISM_DSN")
    if not dsn:
        typer.echo(
            "Error: DATAPRISM_DSN environment variable is required.\n"
            "Set it before running dataprism commands. Example:\n"
            '  $env:DATAPRISM_DSN = "postgresql://user:pass@host:5432/db"',
            err=True,
        )
        raise typer.Exit(code=2)
    return dsn


# ---- table classify --------------------------------------------------


@table_app.command("classify")
def table_classify(
    table: str = typer.Option(
        ...,
        "--table",
        help="Table name, or comma-separated list of names (e.g., "
        "'users' or 'users,orders,customers'). Duplicates deduped.",
    ),
    policy: str = typer.Option(
        ...,
        "--policy",
        help="Policy name (resolves to config/policies/<name>.yaml).",
    ),
    output: OutputFormat = typer.Option(
        OutputFormat.text,
        "--output",
        help="Output format. Only applies in single-table mode; "
        "multi-table mode always uses text progress output.",
    ),
    actor: str = typer.Option(
        "cli",
        "--actor",
        help="Actor name recorded on audit events.",
    ),
) -> None:
    """Classify columns in one or more tables according to a policy.

    The --table option accepts a single table name or a comma-
    separated list. With one table, output mirrors the single-table
    classify (text or JSON). With multiple tables, progress lines
    stream per table and a summary follows.

    Reads DSN from DATAPRISM_DSN env var. The policy is resolved
    against config/policies/<name>.yaml in the project root. Audit
    events go to <project-root>/audit/audit.jsonl.
    """
    # Parse the --table input into a deduplicated list.
    # dict.fromkeys preserves first-occurrence order.
    table_names = list(dict.fromkeys(t.strip() for t in table.split(",") if t.strip()))
    if not table_names:
        typer.echo("Error: --table must specify at least one table name.", err=True)
        raise typer.Exit(code=2)

    # Resolve and validate inputs
    dsn = _read_dsn_from_env()
    policy_path = cli_paths.get_policy_path(policy)
    if not policy_path.exists():
        policies_dir = policy_path.parent
        available = sorted(p.stem for p in policies_dir.glob("*.yaml"))
        avail_str = ", ".join(available) if available else "(none)"
        typer.echo(
            f"Error: Policy '{policy}' not found.\n"
            f"  Looked at: {policy_path}\n"
            f"  Available policies: {avail_str}",
            err=True,
        )
        raise typer.Exit(code=2)

    # Set up dependencies
    try:
        adapter = cli_adapters.select_adapter(dsn)
        normalized_dsn = cli_adapters.normalize_dsn(dsn)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=2) from e

    audit_log_path = cli_paths.get_audit_log_path()
    storage = JsonLinesStorage(audit_log_path)
    audit = AuditService(storage)

    loaded_policy = load_classification_policy(policy_path)

    _run_classify_scan(
        adapter=adapter,
        normalized_dsn=normalized_dsn,
        dsn=dsn,
        tables=table_names,
        loaded_policy=loaded_policy,
        policy_name=policy,
        audit=audit,
        actor=actor,
        output=output,
        audit_log_path=audit_log_path,
    )


def _run_classify_scan(
    *,
    adapter,
    normalized_dsn: str,
    dsn: str,
    tables: list[str],
    loaded_policy,
    policy_name: str,
    audit,
    actor: str,
    output: OutputFormat,
    audit_log_path,
) -> None:
    """Unified classify-scan execution.

    Always calls classify_tables. Branches only on display:

    - Single table (len(tables) == 1): emits the detailed per-column
      text or JSON of PR 10. No progress callbacks (one table doesn't
      need a feed).
    - Multiple tables: streams per-table progress lines via callbacks,
      then emits the final summary.

    After the engine returns, always writes the HTML report and prints
    its path plus the audit log path.
    """
    is_multi = len(tables) > 1
    target_summary = cli_adapters.redact_dsn_for_display(dsn)

    # Progress callbacks are only useful for multi-table runs.
    on_start = None
    on_complete = None
    on_failed = None
    if is_multi:

        def on_start(name: str) -> None:
            typer.echo(render_progress_start(name), nl=False)

        def on_complete(name: str, report) -> None:
            typer.echo(render_progress_complete_continuation(report))

        def on_failed(name: str, err: str) -> None:
            typer.echo(render_progress_error_continuation(err))

    try:
        adapter.connect(normalized_dsn)
        try:
            scan_report = classify_tables(
                adapter,
                tables,
                loaded_policy,
                audit,
                policy_name=policy_name,
                target_summary=target_summary,
                actor=actor,
                on_table_start=on_start,
                on_table_complete=on_complete,
                on_table_failed=on_failed,
            )
        finally:
            adapter.close()
    except AdapterError as e:
        typer.echo(f"Database error: {e}", err=True)
        raise typer.Exit(code=1) from e

    # Terminal display.
    #
    # Note on exit codes: per-table failures (missing table, denied
    # permission, etc.) are findings, not program errors - the program
    # ran, it just didn't find what the user expected. We surface these
    # in stderr text + the HTML report's Errors section, but exit 0.
    # Reserve non-zero exits for program-level failures (connection
    # refused, missing policy file, etc.) and for misuse.
    if is_multi:
        typer.echo(render_scan_summary(scan_report))
    elif output == OutputFormat.json:
        # Single-table JSON output: the per-table report only, not the
        # whole ScanReport. Preserves PR 10's contract for scripting
        # callers that expect a TableClassificationReport shape.
        # If the table failed, render the failure as the JSON document
        # instead, so callers get something parseable rather than empty.
        if scan_report.tables:
            typer.echo(render_json(scan_report.tables[0]))
        else:
            failed = scan_report.failed_tables[0]
            typer.echo(
                json.dumps(
                    {"table": failed.name, "error": failed.error},
                    indent=2,
                )
            )
    else:
        # Single-table text output: detailed per-column rendering, or
        # the failure surfaced to stderr for visibility (but no exit).
        if scan_report.tables:
            typer.echo(render_text(scan_report.tables[0]))
        else:
            failed = scan_report.failed_tables[0]
            typer.echo(f"Table not scanned: {failed.error}", err=True)

    # HTML report - always written, even on failure or JSON mode.
    report_path = cli_paths.get_report_path(scan_report.completed_at)
    report_path.write_text(
        render_html_report(scan_report, audit_log_path=audit_log_path),
        encoding="utf-8",
    )

    # Trailer (text mode and multi-table only - JSON mode keeps stdout
    # parseable).
    if not (is_multi is False and output == OutputFormat.json):
        typer.echo(f"Report: {report_path}")
        typer.echo(f"Audit log: {audit_log_path}")


# ---- table candidates ------------------------------------------------


@table_app.command("candidates")
def table_candidates(
    policy: str = typer.Option(
        ...,
        "--policy",
        help="Policy name (resolves to config/policies/<name>.yaml).",
    ),
    schema: str = typer.Option(
        None,
        "--schema",
        help="Schema name to narrow the listing. Omit for the "
        "adapter's default scope (public for Postgres; all tables "
        "for SQLite).",
    ),
    output: OutputFormat = typer.Option(
        OutputFormat.text,
        "--output",
        help="Output format.",
    ),
) -> None:
    """List candidate tables, annotated with name-rule match counts.

    Walks tables in scope (per the adapter's default or --schema),
    and for each, counts how many columns match the policy's
    NAME-BASED rules (regex with target=column_name; dictionary).
    Statistical and value-based rules are not evaluated here -
    those require sampling, which would defeat the purpose of a
    cheap pre-scan.

    Tables are sorted by match count descending (most likely scan
    targets first), then alphabetically.

    Match counts are a heuristic: a 0-match table may still
    contain sensitive data in oddly-named columns. Classify to
    be sure.
    """
    dsn = _read_dsn_from_env()
    policy_path = cli_paths.get_policy_path(policy)
    if not policy_path.exists():
        policies_dir = policy_path.parent
        available = sorted(p.stem for p in policies_dir.glob("*.yaml"))
        avail_str = ", ".join(available) if available else "(none)"
        typer.echo(
            f"Error: Policy '{policy}' not found.\n"
            f"  Looked at: {policy_path}\n"
            f"  Available policies: {avail_str}",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        adapter = cli_adapters.select_adapter(dsn)
        normalized_dsn = cli_adapters.normalize_dsn(dsn)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=2) from e

    loaded_policy = load_classification_policy(policy_path)

    try:
        adapter.connect(normalized_dsn)
        try:
            candidates = list_table_candidates(adapter, loaded_policy, schema=schema)
        finally:
            adapter.close()
    except AdapterError as e:
        typer.echo(f"Database error: {e}", err=True)
        raise typer.Exit(code=1) from e

    if output == OutputFormat.json:
        typer.echo(render_candidates_json(candidates, schema=schema))
    else:
        typer.echo(render_candidates_text(candidates, schema=schema))


# ---- audit verify ----------------------------------------------------


@audit_app.command("verify")
def audit_verify(
    output: OutputFormat = typer.Option(
        OutputFormat.text,
        "--output",
        help="Output format.",
    ),
) -> None:
    """Verify the audit log's hash chain integrity.

    Reads <project-root>/audit/audit.jsonl, recomputes the hash
    chain, and reports any tampering.
    """
    audit_log_path = cli_paths.get_audit_log_path()

    if not audit_log_path.exists():
        typer.echo(
            f"Error: Audit log not found at {audit_log_path}.\n"
            f"Run a dataprism command first to create one.",
            err=True,
        )
        raise typer.Exit(code=2)

    storage = JsonLinesStorage(audit_log_path)

    # JsonLinesStorage.verify() returns None on success, raises
    # ChainVerificationError if tampering is detected.
    try:
        storage.verify()
    except ChainVerificationError as e:
        if output == OutputFormat.json:
            result = {
                "status": "tampered",
                "audit_log": str(audit_log_path),
                "error": str(e),
            }
            typer.echo(json.dumps(result, indent=2))
        else:
            typer.echo(f"Audit log verification FAILED: {audit_log_path}", err=True)
            typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    # Success path
    if output == OutputFormat.json:
        result = {"status": "ok", "audit_log": str(audit_log_path)}
        typer.echo(json.dumps(result, indent=2))
    else:
        typer.echo(f"Audit log verified: {audit_log_path}")
        typer.echo("Hash chain is intact.")


# ---- Entry point -----------------------------------------------------


def main() -> None:
    """Entry point called from the dataprism script.

    The pyproject.toml [project.scripts] section maps the `dataprism`
    command to this function (`dataprism.cli:main`). For that to work,
    the cli/__init__.py module needs to re-export main.
    """
    app()


if __name__ == "__main__":
    main()
