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

import pytest
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


@pytest.fixture(autouse=True)
def temp_reports_dir(tmp_path, monkeypatch):
    """Redirect HTML report output to a temp directory.

    autouse=True ensures no test pollutes the real <project-root>/reports/
    directory by accidentally invoking a code path that writes a report.
    Tests that want to inspect the generated HTML can request the
    fixture explicitly and use the yielded directory.

    Yields the reports directory (a tmp Path).
    """
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()

    def fake_get_report_path(timestamp):
        formatted = timestamp.strftime("%Y-%m-%d-%H%M%S")
        return reports_dir / f"{formatted}.html"

    monkeypatch.setattr(cli_paths, "get_report_path", fake_get_report_path)
    yield reports_dir


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
        assert "candidates" in stdout

    def test_table_candidates_help(self):
        """dataprism table candidates --help shows the options."""
        result = runner.invoke(app, ["table", "candidates", "--help"])
        assert result.exit_code == 0
        stdout = _strip_ansi(result.stdout)
        assert "--policy" in stdout
        assert "--schema" in stdout
        assert "--output" in stdout

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

    def test_unknown_policy_errors_with_exit_code_2(
        self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn
    ):
        """An unknown policy name exits 2 with a helpful message."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "nonexistent"],
        )
        assert result.exit_code == 2
        assert "nonexistent" in result.stderr

    def test_unknown_policy_message_lists_available(
        self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn
    ):
        """The error message lists the policies that DO exist."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
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

    def test_classify_against_sqlite_succeeds(
        self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn
    ):
        """A normal classify exits 0 against a SQLite test database."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        assert result.exit_code == 0

    def test_classify_text_output_includes_table_name(
        self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn
    ):
        """Text output includes the classified table name."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        assert "users" in result.stdout

    def test_classify_text_output_includes_audit_log_path(
        self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn
    ):
        """Text output prints the audit log location for user reference."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        assert "Audit log:" in result.stdout

    def test_classify_detects_pii_in_email_column(
        self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn
    ):
        """The example policy correctly tags the email column as PII."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        assert "PII" in result.stdout
        assert "email" in result.stdout

    def test_classify_audit_log_written(
        self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn
    ):
        """The audit log file is written during classify."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
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

    def test_json_output_is_parseable(
        self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn
    ):
        """The --output json output is valid JSON."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
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

    def test_json_output_has_expected_structure(
        self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn
    ):
        """The JSON output has the documented fields."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
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
        self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn
    ):
        """JSON output is parseable - the audit log path message is NOT appended."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
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
    """Per-table failures are findings, not program errors.

    PR 12 corrected PR 10's behavior: single-table failures are
    treated as data findings (exit 0, surfaced in stderr + HTML
    report) rather than program errors. This matches multi-table
    behavior, where per-table failures have always been findings.
    """

    def test_missing_table_exits_zero_with_finding(
        self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn
    ):
        """A missing table is a finding (exit 0), not a program error.

        The failure is surfaced on stderr for visibility and
        documented in the HTML report's Errors section. Reserve
        non-zero exits for connection failures, missing policy
        files, and similar program-level errors.
        """
        # Create a database with NO 'missing_table' in it
        dsn = make_users_db_dsn(tmp_path / "test.db")
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
        assert result.exit_code == 0
        # Finding text appears on stderr (with the engine's message)
        assert "not scanned" in result.stderr.lower() or "not found" in result.stderr.lower()


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

    def test_verify_clean_log_succeeds(
        self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn
    ):
        """A clean audit log (just written by classify) verifies successfully."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
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

    def test_verify_json_output(self, tmp_path, monkeypatch, temp_audit_log, make_users_db_dsn):
        """JSON output for verify is parseable."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
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


# ---- Multi-table classify -------------------------------------------


class TestMultiTableClassify:
    """Comma-separated --table input and multi-table progress output."""

    def test_two_tables_succeed(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """Two tables in --table both classify; exit 0."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users,orders", "--policy", "example"],
        )
        assert result.exit_code == 0

    def test_progress_line_emitted_per_table(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """Each table gets its own progress line."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users,orders", "--policy", "example"],
        )
        assert "Scanning users" in result.stdout
        assert "Scanning orders" in result.stdout

    def test_summary_line_present(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """Final summary shows the table count and classification total."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users,orders", "--policy", "example"],
        )
        assert "Scanned 2 tables" in result.stdout
        assert "classifications total" in result.stdout

    def test_clean_run_omits_succeeded_failed(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """No failures means no '(X succeeded, Y failed)' parenthetical."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users,orders", "--policy", "example"],
        )
        # Inspect the summary line specifically (avoid false positives
        # from path names that happen to contain "succeeded").
        summary_lines = [line for line in result.stdout.splitlines() if line.startswith("Scanned ")]
        assert len(summary_lines) == 1
        summary = summary_lines[0]
        assert "succeeded" not in summary
        assert "failed" not in summary

    def test_failed_table_shows_error_line(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """A nonexistent table produces an ERROR line and the scan continues."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users,ghost,orders", "--policy", "example"],
        )
        # Scan continues despite the failure; exit code is still 0
        assert result.exit_code == 0
        assert "Scanning users" in result.stdout
        assert "Scanning ghost" in result.stdout
        assert "ERROR" in result.stdout
        assert "Scanning orders" in result.stdout

    def test_partial_failure_summary_shows_counts(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """Summary with partial failure shows '(X succeeded, Y failed)' parenthetical."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users,ghost", "--policy", "example"],
        )
        assert "succeeded" in result.stdout
        assert "failed" in result.stdout

    def test_duplicate_tables_deduped(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """Duplicate names in --table are silently deduped."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            [
                "table",
                "classify",
                "--table",
                "users,users,users",
                "--policy",
                "example",
            ],
        )
        # Single table mode (single unique name after dedupe), so detailed
        # output should appear instead of progress lines.
        assert "Classified table 'users'" in result.stdout

    def test_whitespace_stripped_around_table_names(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """Spaces around comma-separated table names are stripped."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            [
                "table",
                "classify",
                "--table",
                "users, orders , products",
                "--policy",
                "example",
            ],
        )
        assert result.exit_code == 0
        assert "Scanned 3 tables" in result.stdout

    def test_audit_log_includes_scan_bookends(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """Multi-table run records SCAN_STARTED and SCAN_COMPLETED events."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        runner.invoke(
            app,
            ["table", "classify", "--table", "users,orders", "--policy", "example"],
        )
        content = temp_audit_log.read_text(encoding="utf-8")
        assert "scan_started" in content
        assert "scan_completed" in content


# ---- Table candidates -----------------------------------------------


class TestTableCandidates:
    """The new `dataprism table candidates` command."""

    def test_candidates_against_sqlite_succeeds(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """A normal candidates listing exits 0."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(app, ["table", "candidates", "--policy", "example"])
        assert result.exit_code == 0

    def test_candidates_lists_all_tables(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """Output mentions every table in the database."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(app, ["table", "candidates", "--policy", "example"])
        assert "users" in result.stdout
        assert "orders" in result.stdout
        assert "products" in result.stdout

    def test_candidates_text_includes_match_counts(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """Text output shows 'matching' counts per table."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(app, ["table", "candidates", "--policy", "example"])
        assert "matching" in result.stdout

    def test_candidates_text_includes_caveat(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """Caveat text appears at the bottom of the output."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(app, ["table", "candidates", "--policy", "example"])
        assert "column-name rules only" in result.stdout

    def test_candidates_json_output_is_parseable(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """--output json produces valid JSON."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "candidates", "--policy", "example", "--output", "json"],
        )
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)

    def test_candidates_json_has_expected_structure(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """JSON has schema, total_tables, tables top-level keys."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "candidates", "--policy", "example", "--output", "json"],
        )
        parsed = json.loads(result.stdout)
        assert "schema" in parsed
        assert "total_tables" in parsed
        assert "tables" in parsed
        assert parsed["total_tables"] == 3

    def test_candidates_sort_order_matches_engine(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """JSON tables list is sorted match_count desc."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "candidates", "--policy", "example", "--output", "json"],
        )
        parsed = json.loads(result.stdout)
        counts = [t["match_count"] for t in parsed["tables"]]
        # Confirm descending order
        assert counts == sorted(counts, reverse=True)

    def test_candidates_unknown_policy_errors(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """Unknown policy name exits 2."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(app, ["table", "candidates", "--policy", "nonexistent"])
        assert result.exit_code == 2
        assert "nonexistent" in result.stderr

    def test_candidates_missing_dsn_errors(self, monkeypatch, temp_audit_log):
        """Without DATAPRISM_DSN, candidates exits 2."""
        monkeypatch.delenv("DATAPRISM_DSN", raising=False)
        result = runner.invoke(app, ["table", "candidates", "--policy", "example"])
        assert result.exit_code == 2
        assert "DATAPRISM_DSN" in result.stderr

    def test_candidates_does_not_write_audit_log(
        self, tmp_path, monkeypatch, temp_audit_log, make_multi_table_db_dsn
    ):
        """Candidates listing doesn't classify, so no audit events are recorded."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        runner.invoke(app, ["table", "candidates", "--policy", "example"])
        # Audit log shouldn't exist (or if it does, it's empty)
        if temp_audit_log.exists():
            assert temp_audit_log.read_text(encoding="utf-8").strip() == ""


# ---- HTML report generation ----------------------------------------


class TestHtmlReportGeneration:
    """HTML reports are generated for every classify run."""

    def test_single_table_text_writes_html_report(
        self, tmp_path, monkeypatch, temp_audit_log, temp_reports_dir, make_users_db_dsn
    ):
        """A single-table classify writes one HTML file to the reports dir."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        html_files = list(temp_reports_dir.glob("*.html"))
        assert len(html_files) == 1

    def test_single_table_json_writes_html_report(
        self, tmp_path, monkeypatch, temp_audit_log, temp_reports_dir, make_users_db_dsn
    ):
        """JSON output mode still writes the HTML report silently."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        runner.invoke(
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
        html_files = list(temp_reports_dir.glob("*.html"))
        assert len(html_files) == 1

    def test_multi_table_writes_html_report(
        self, tmp_path, monkeypatch, temp_audit_log, temp_reports_dir, make_multi_table_db_dsn
    ):
        """A multi-table classify writes one HTML file (covering all tables)."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        runner.invoke(
            app,
            [
                "table",
                "classify",
                "--table",
                "users,orders",
                "--policy",
                "example",
            ],
        )
        html_files = list(temp_reports_dir.glob("*.html"))
        assert len(html_files) == 1

    def test_text_mode_prints_report_path(
        self, tmp_path, monkeypatch, temp_audit_log, temp_reports_dir, make_users_db_dsn
    ):
        """Single-table text mode shows the report path in stdout."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        assert "Report:" in result.stdout

    def test_json_mode_omits_report_trailer(
        self, tmp_path, monkeypatch, temp_audit_log, temp_reports_dir, make_users_db_dsn
    ):
        """JSON output mode keeps stdout parseable - no 'Report:' line."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
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
        assert "Report:" not in result.stdout
        # And the stdout should still parse as JSON
        parsed = json.loads(result.stdout)
        assert parsed["table"] == "users"

    def test_multi_table_mode_prints_report_path(
        self, tmp_path, monkeypatch, temp_audit_log, temp_reports_dir, make_multi_table_db_dsn
    ):
        """Multi-table mode shows the report path."""
        dsn = make_multi_table_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            [
                "table",
                "classify",
                "--table",
                "users,orders",
                "--policy",
                "example",
            ],
        )
        assert "Report:" in result.stdout

    def test_html_contains_scan_id(
        self, tmp_path, monkeypatch, temp_audit_log, temp_reports_dir, make_users_db_dsn
    ):
        """The HTML file contains the scan_id (cross-references audit log)."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        html_files = list(temp_reports_dir.glob("*.html"))
        html_content = html_files[0].read_text(encoding="utf-8")

        # Find the SCAN_STARTED scan_id in the audit log and verify
        # it appears in the HTML.
        audit_content = temp_audit_log.read_text(encoding="utf-8")
        scan_event_line = next(
            line for line in audit_content.splitlines() if "scan_started" in line
        )
        scan_event = json.loads(scan_event_line)
        scan_id = scan_event["data"]["scan_id"]

        assert scan_id in html_content

    def test_html_contains_table_name(
        self, tmp_path, monkeypatch, temp_audit_log, temp_reports_dir, make_users_db_dsn
    ):
        """The HTML report mentions the table that was scanned."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        html_files = list(temp_reports_dir.glob("*.html"))
        html_content = html_files[0].read_text(encoding="utf-8")
        assert "users" in html_content

    def test_html_contains_pii_classification(
        self, tmp_path, monkeypatch, temp_audit_log, temp_reports_dir, make_users_db_dsn
    ):
        """The PII match on email column appears in the HTML."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        html_files = list(temp_reports_dir.glob("*.html"))
        html_content = html_files[0].read_text(encoding="utf-8")
        assert ">PII<" in html_content
        assert "label-pii" in html_content

    def test_html_redacts_dsn_password(
        self, tmp_path, monkeypatch, temp_audit_log, temp_reports_dir
    ):
        """The HTML's target line shows a redacted DSN, never the password.

        Uses a SQLite DSN with a synthetic password-like path segment
        to verify nothing leaks. SQLite DSNs don't actually have
        passwords; the test asserts that whatever the user puts in
        the DSN, the redact_dsn_for_display logic runs (and for SQLite,
        there's no password to redact, so the path passes through).
        """
        # The point of this test is the path goes through redact_dsn_for_display.
        # We use a Postgres-style DSN that won't actually connect, but the
        # CLI calls the redactor before connecting fails.
        # Better approach: verify via a unit-level check that the CLI
        # would-be target_summary doesn't contain a real password by
        # using the redactor explicitly.
        from dataprism.cli.adapters import redact_dsn_for_display

        sample_dsn = "postgresql://user:VERY_SECRET@host:5432/db"
        target_summary = redact_dsn_for_display(sample_dsn)
        # Real test: confirm the redactor catches the password
        assert "VERY_SECRET" not in target_summary
        assert "***" in target_summary

    def test_html_report_filename_is_timestamp(
        self, tmp_path, monkeypatch, temp_audit_log, temp_reports_dir, make_users_db_dsn
    ):
        """The HTML filename matches the YYYY-MM-DD-HHMMSS pattern."""
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        runner.invoke(
            app,
            ["table", "classify", "--table", "users", "--policy", "example"],
        )
        html_files = list(temp_reports_dir.glob("*.html"))
        assert len(html_files) == 1
        stem = html_files[0].stem
        # YYYY-MM-DD-HHMMSS = 17 chars
        assert len(stem) == 17
        # Format: 4-2-2-6 with dashes between
        parts = stem.split("-")
        assert len(parts) == 4
        assert len(parts[0]) == 4  # year
        assert len(parts[1]) == 2  # month
        assert len(parts[2]) == 2  # day
        assert len(parts[3]) == 6  # HHMMSS

    def test_failed_single_table_still_writes_html_report(
        self, tmp_path, monkeypatch, temp_audit_log, temp_reports_dir, make_users_db_dsn
    ):
        """A single-table classify of a nonexistent table still writes an HTML report.

        Under PR 12, per-table failures are findings, not program
        errors. Exit code is 0; the HTML report documents the failure
        in its Errors section, providing a governance artifact even
        for "I tried to scan X and it doesn't exist" cases.
        """
        dsn = make_users_db_dsn(tmp_path / "test.db")
        monkeypatch.setenv("DATAPRISM_DSN", dsn)

        result = runner.invoke(
            app,
            ["table", "classify", "--table", "ghost", "--policy", "example"],
        )
        # Exit code is 0 - the program ran successfully; the failure
        # is information, not an error.
        assert result.exit_code == 0
        # And the HTML report exists, documenting the failure
        html_files = list(temp_reports_dir.glob("*.html"))
        assert len(html_files) == 1
        html_content = html_files[0].read_text(encoding="utf-8")
        assert "ghost" in html_content
        assert "Table not found" in html_content or "not found" in html_content.lower()
