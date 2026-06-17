"""Path resolution for the CLI.

The CLI assumes dataprism runs in a self-contained project layout:

    <project-root>/
    ├── src/dataprism/         <- source code (this lives in cli/paths.py)
    ├── config/policies/       <- user-authored YAML policies
    ├── audit/                 <- auto-created on first run
    │   └── audit.jsonl
    └── pyproject.toml         <- presence verifies project root

Functions in this module find the project root by walking up from
the dataprism source code's location. This works for developing
dataprism (where source lives in the project tree) but fails if
dataprism is installed via pip (where source lives in site-packages).
The pip-install case is deferred; see docs/ARCHITECTURE.md Section 8
"PyPI distribution / workspace model".
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import dataprism


def get_project_root() -> Path:
    """Find the dataprism project root.

    Walks up from the dataprism package source location to find a
    directory containing pyproject.toml.

    Returns:
        The absolute path to the project root.

    Raises:
        RuntimeError: If walking up from the source doesn't find a
            project root. This typically means dataprism is installed
            via pip (running from site-packages, not a project tree).
    """
    # dataprism.__file__ -> .../src/dataprism/__init__.py
    # parent.parent.parent -> .../src/.. = the project root
    package_init = Path(dataprism.__file__).resolve()
    candidate = package_init.parent.parent.parent

    if not (candidate / "pyproject.toml").exists():
        raise RuntimeError(
            f"Could not locate dataprism project root. "
            f"Walked up from {package_init} and arrived at {candidate}, "
            f"but no pyproject.toml found there. "
            f"dataprism currently requires running from a self-contained "
            f"project layout (cloned repository). The pip-install workflow "
            f"is not yet supported."
        )

    return candidate


def get_audit_log_path() -> Path:
    """Return the path to the audit log file.

    Always returns <project-root>/audit/audit.jsonl. Creates the
    audit/ directory if it doesn't exist (idempotent).
    """
    root = get_project_root()
    audit_dir = root / "audit"
    audit_dir.mkdir(exist_ok=True)
    return audit_dir / "audit.jsonl"


def get_policy_path(name: str) -> Path:
    """Resolve a policy name to its file path.

    A policy NAME (without extension or path) maps to:

        <project-root>/config/policies/<name>.yaml

    Args:
        name: Short policy name (e.g., "example", "default", "strict").

    Returns:
        The absolute path to the policy file. Does NOT verify
        that the file exists - callers handle the "not found" case
        themselves so they can give specific error messages.
    """
    root = get_project_root()
    return root / "config" / "policies" / f"{name}.yaml"


def get_report_path(timestamp: datetime) -> Path:
    """Build the path for an HTML scan report.

    The path is <project-root>/reports/<YYYY-MM-DD-HHMMSS>.html.
    Creates the reports/ directory if it doesn't exist (idempotent).

    The filename is derived from the passed-in timestamp, so callers
    can ensure the filename matches a known scan event (typically a
    ScanReport's completed_at). This makes the file naturally
    sortable by chronological order and avoids mismatch between the
    filename and the report's recorded times.

    Args:
        timestamp: The reference time. Typically scan_report.completed_at.
            Both naive and tz-aware datetimes work; the encoded time is
            whatever strftime renders.

    Returns:
        The absolute path to the HTML report file. Does NOT create
        the file - the caller writes content to it.
    """
    root = get_project_root()
    reports_dir = root / "reports"
    reports_dir.mkdir(exist_ok=True)
    formatted = timestamp.strftime("%Y-%m-%d-%H%M%S")
    return reports_dir / f"{formatted}.html"
