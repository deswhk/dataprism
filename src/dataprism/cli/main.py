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
from dataprism.classification.table import classify_table
from dataprism.cli import adapters as cli_adapters
from dataprism.cli import paths as cli_paths
from dataprism.cli.render import render_json, render_text
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
        help="Table to classify (e.g., 'users' or 'public.users').",
    ),
    policy: str = typer.Option(
        ...,
        "--policy",
        help="Policy name (resolves to config/policies/<name>.yaml).",
    ),
    output: OutputFormat = typer.Option(
        OutputFormat.text,
        "--output",
        help="Output format.",
    ),
    actor: str = typer.Option(
        "cli",
        "--actor",
        help="Actor name recorded on audit events.",
    ),
) -> None:
    """Classify columns in a table according to a policy.

    Reads DSN from DATAPRISM_DSN env var. The policy is resolved
    against config/policies/<name>.yaml in the project root. Audit
    events go to <project-root>/audit/audit.jsonl.
    """
    # Resolve and validate inputs
    dsn = _read_dsn_from_env()
    policy_path = cli_paths.get_policy_path(policy)
    if not policy_path.exists():
        # List what IS available to be helpful
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

    # Connect, run, disconnect
    try:
        adapter.connect(normalized_dsn)
        try:
            report = classify_table(
                adapter,
                table,
                loaded_policy,
                audit,
                actor=actor,
            )
        finally:
            adapter.close()
    except AdapterError as e:
        typer.echo(f"Database error: {e}", err=True)
        raise typer.Exit(code=1) from e

    # Render and print
    if output == OutputFormat.json:
        typer.echo(render_json(report))
    else:
        typer.echo(render_text(report))
        typer.echo(f"Audit log: {audit_log_path}")


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
