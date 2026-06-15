"""End-to-end tests for the dataprism CLI commands.

Tests use typer.testing.CliRunner to invoke commands in-process. Real
SQLite databases are created in temp directories for tests that need
to actually classify; the get_audit_log_path function is monkey-patched
to redirect audit output to a temp location so test runs don't pollute
the real audit log.

The CLI's DSN comes from DATAPRISM_DSN env var; tests use pytest's
monkeypatch fixture to set/unset this per-test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from dataprism.cli import paths as cli_paths
from dataprism.cli.main import app

# Strip ANSI escape sequences from rich-formatted output.
# rich (used by typer) inserts ANSI codes for bold, color, etc. even
# when stdout is being captured. The codes can split logical words
# like '--table' across multiple escape sequences, breaking simple
# substring assertions. This regex removes them so tests can match
# plain text.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHfABCDJ]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


runner = CliRunner()


@pytest.fixture(autouse=True)
def wide_terminal(monkeypatch):
    """Force rich to render at a wide, fixed width in all tests.

    rich (used by typer) detects terminal width and truncates help
    output to fit. In CI environments (no TTY), the default width is
    narrow and option names get truncated mid-word. Setting COLUMNS
    to a large fixed value makes rendering reproducible across local
    (Windows) and CI (Linux) environments.
    """
    monkeypatch.setenv("COLUMNS", "200")


# ---- Test helpers ---------------------------------------------------
#
# NOTE: _make_users_db duplicates make_users_db from tests/adapters/
# fixtures.py. This duplication is intentional - pytest test
# subpackages don't share fixtures (no tests/__init__.py). Documented
# in docs/ARCHITECTURE.md Section 8 "Test helper consolidation".


def _make_users_db(path: Path) -> str:
    """Create a SQLite users database for CLI tests.

    Returns the DSN. The schema has columns that match common
    classification rules (email, name) so we can verify the CLI
    actually classifies them.
    """
    dsn = f"sqlite:///{path}"
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE users (id INTEGER, email TEXT, name TEXT)"))
            conn.execute(text("INSERT INTO users VALUES (1, 'alice@example.com', 'Alice')"))
            conn.execute(text("INSERT INTO users VALUES (2, 'bob@example.com', 'Bob')"))
    finally:
        engine.dispose()
    return dsn


@pytest.fixture
def temp_audit_log(tmp_path, monkeypatch):
    """Redirect the audit log to a temp directory for the test.

    Yields the temp path. After the test, monkeypatch automatically
    restores the original behavior.
    """
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    audit_path = audit_dir / "audit.jsonl"

    # Monkey-patch get_audit_log_path in the locations where main.py imports it
    monkeypatch.setattr(cli_paths, "get_audit_log_path", lambda: audit_path)

    yield audit_path


# ---- Help text tests ------------------------------------------------


class TestHelpText:
    """Help text presence at every command level."""

    def test_top_level_help(self):
        """dataprism --help shows the top-level usage."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        stdout = _strip_ansi(result.stdout)
        assert "table" in stdout
        assert "audit" in stdout

    def test_table_group_help(self):
        """dataprism table --help shows the table subcommands."""
        result = runner.invoke(app, ["table", "--help"])
        assert result.exit_code == 0
        stdout = _strip_ansi(result.stdout)
        assert "classify" in stdout

    def test_table_classify_help(self):
        """dataprism table classify --help shows the options."""
        result = runner.invoke(app, ["table", "classify", "--help"])
        assert result.exit_code == 0
        stdout = _strip_ansi(result.stdout)
        assert "--table" in stdout
        assert "--policy" in stdout
        assert "--output" in stdout

    def test_audit_group_help(self):
        """dataprism audit --help shows the audit subcommands."""
        result = runner.invoke(app, ["audit", "--help"])
        assert result.exit_code == 0
        stdout = _strip_ansi(result.stdout)
        assert "verify" in stdout

    def test_audit_verify_help(self):
        """dataprism audit verify --help shows the options."""
        result = runner.invoke(app, ["audit", "verify", "--help"])
        assert result.exit_code == 0
        stdout = _strip_ansi(result.stdout)
        assert "--output" in stdout

    def test_no_args_shows_help(self):
        """dataprism (no args) shows help (per no_args_is_help=True)."""
        result = runner.invoke(app, [])
        stdout = _strip_ansi(result.stdout)
        # Typer/click returns exit code 2 when showing help due to no_args_is_help
        assert "Usage:" in stdout or "table" in stdout


# ---- DSN env var handling -------------------------------------------


class TestDsnEnvVar:
    """The CLI requires DATAPRISM_DSN to be set."""

    def test_missing_dsn_errors_with_exit_code_2(self, monkeypatch, temp_audit_log):
        """Without DATAPRISM_DSN, classify exits 2 with a clear message."""
        monkeypatch.delenv("DATAPRISM_DSN", raising=False)
        result = runner.invoke(
            app, ["table", "classify", "--table", "users", "--policy", "example"]
        )
        assert result.exit_code == 2
        assert "DATAPRISM_DSN" in result.stderr

    def test_missing_dsn_message_suggests_how_to_set(self, monkeypatch, temp_audit_log):
        """The error message guides the user toward fixing it."""
        monkeypatch.delenv("DATAPRISM_DSN", raising=False)
        result = runner.invoke(
            app, ["table", "classify", "--table", "users", "--policy", "example"]
        )
        assert "Set it" in result.stderr or "DATAPRISM_DSN" in result.stderr


# ---- Policy resolution ----------------------------------------------


class TestPolicyResolution:
    """Policy name resolves to config/policies/<name>.yaml."""

    def test_unknown_policy_errors_with_exit_code_2(self, tmp_path, monkeypatch, temp_audit_log):
        """An unknown policy name exits 2 with a helpful message."""
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "nonexistent"],
        )
        assert result.exit_code == 2
        assert "nonexistent" in result.stderr

    def test_unknown_policy_message_lists_available(self, tmp_path, monkeypatch, temp_audit_log):
        """The error message lists the policies that DO exist."""
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "nonexistent"],
        )
        # 'example' is the policy that exists in config/policies/
        assert "example" in result.stderr


# ---- Successful classify --------------------------------------------


class TestClassifySuccess:
    """A successful classify run produces expected output."""

    def test_classify_against_sqlite_succeeds(self, tmp_path, monkeypatch, temp_audit_log):
        """A normal classify exits 0 against a SQLite test database."""
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        assert result.exit_code == 0

    def test_classify_text_output_includes_table_name(self, tmp_path, monkeypatch, temp_audit_log):
        """Text output includes the classified table name."""
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        assert "users" in result.stdout

    def test_classify_text_output_includes_audit_log_path(
        self, tmp_path, monkeypatch, temp_audit_log
    ):
        """Text output prints the audit log location for user reference."""
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        assert "Audit log:" in result.stdout

    def test_classify_detects_pii_in_email_column(self, tmp_path, monkeypatch, temp_audit_log):
        """The example policy correctly tags the email column as PII."""
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        assert "PII" in result.stdout
        assert "email" in result.stdout

    def test_classify_audit_log_written(self, tmp_path, monkeypatch, temp_audit_log):
        """The audit log file is written during classify."""
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        assert temp_audit_log.exists()
        # Should have multiple events: STARTED, per-column RUNs, COMPLETED
        content = temp_audit_log.read_text(encoding="utf-8")
        assert len(content.strip().splitlines()) > 0


class TestClassifyJsonOutput:
    """JSON output mode produces parseable, structured output."""

    def test_json_output_is_parseable(self, tmp_path, monkeypatch, temp_audit_log):
        """The --output json output is valid JSON."""
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            [
                "table",
                "classify",
                "--table",
                "users",
                "--policy",
                "example",
                "--output",
                "json",
            ],
        )
        assert result.exit_code == 0
        # Should be parseable
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)

    def test_json_output_has_expected_structure(self, tmp_path, monkeypatch, temp_audit_log):
        """The JSON output has the documented fields."""
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            [
                "table",
                "classify",
                "--table",
                "users",
                "--policy",
                "example",
                "--output",
                "json",
            ],
        )
        parsed = json.loads(result.stdout)
        assert parsed["table"] == "users"
        assert "columns_attempted" in parsed
        assert "matches_by_column" in parsed
        assert "errors" in parsed

    def test_json_output_does_not_include_audit_log_path(
        self, tmp_path, monkeypatch, temp_audit_log
    ):
        """JSON output is parseable - the audit log path message is NOT appended."""
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            [
                "table",
                "classify",
                "--table",
                "users",
                "--policy",
                "example",
                "--output",
                "json",
            ],
        )
        assert "Audit log:" not in result.stdout


# ---- Table not found error path -------------------------------------


class TestClassifyTableErrors:
    """Database-side errors propagate cleanly."""

    def test_missing_table_errors_with_exit_code_1(self, tmp_path, monkeypatch, temp_audit_log):
        """A table not in the database exits 1 with an adapter error."""
        # Create a database with NO 'missing_table' in it
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            [
                "table",
                "classify",
                "--table",
                "missing_table",
                "--policy",
                "example",
            ],
        )
        assert result.exit_code == 1
        assert "Database error" in result.stderr or "Table not found" in result.stderr


# ---- Audit verify ----------------------------------------------------


class TestAuditVerify:
    """The audit verify command checks chain integrity."""

    def test_verify_missing_log_errors_with_exit_code_2(self, tmp_path, monkeypatch):
        """Verifying when no audit log exists exits 2 with a helpful message."""
        # Point audit log at a path that doesn't exist
        missing_path = tmp_path / "nonexistent" / "audit.jsonl"
        monkeypatch.setattr(cli_paths, "get_audit_log_path", lambda: missing_path)

        result = runner.invoke(app, ["audit", "verify"])
        assert result.exit_code == 2
        assert "not found" in result.stderr.lower()

    def test_verify_clean_log_succeeds(self, tmp_path, monkeypatch, temp_audit_log):
        """A clean audit log (just written by classify) verifies successfully."""
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        # First, generate some audit events
        runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )

        # Now verify
        result = runner.invoke(app, ["audit", "verify"])
        assert result.exit_code == 0
        assert "verified" in result.stdout.lower() or "intact" in result.stdout.lower()

    def test_verify_json_output(self, tmp_path, monkeypatch, temp_audit_log):
        """JSON output for verify is parseable."""
        dsn = _make_users_db(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        # Generate some events
        runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )

        # Verify with json output
        result = runner.invoke(app, ["audit", "verify", "--output", "json"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "ok"
        assert "audit_log" in parsed
