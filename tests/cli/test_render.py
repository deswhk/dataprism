"""Tests for dataprism.cli.render.

Text and JSON rendering of TableClassificationReport. The text
renderer produces human-readable output; the JSON renderer
delegates to Pydantic's model_dump_json().

Tests use small helper functions to construct sample reports with
known shapes (clean run, with errors, no matches, etc.) and verify
the rendered output contains the expected information.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from dataprism.classification.candidates import TableCandidate
from dataprism.classification.results import ClassificationResult
from dataprism.classification.table import (
    ColumnError,
    FailedTable,
    ScanReport,
    TableClassificationReport,
)
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

# ---- Test helpers ---------------------------------------------------


def _make_result(
    column_name: str,
    rule_name: str = "test_rule",
    rule_type: str = "regex",
    classification: str = "PII",
) -> ClassificationResult:
    """Construct a ClassificationResult with reasonable defaults."""
    return ClassificationResult(
        column_name=column_name,
        classification=classification,
        rule_name=rule_name,
        rule_type=rule_type,
    )


def _make_clean_report() -> TableClassificationReport:
    """A successful run with some columns matching."""
    return TableClassificationReport(
        table="users",
        columns_attempted=3,
        matches_by_column={
            "id": [],
            "email": [_make_result("email", rule_name="email_pattern")],
            "name": [],
        },
        errors=[],
    )


def _make_report_with_errors() -> TableClassificationReport:
    """A run with some successes and some failures."""
    return TableClassificationReport(
        table="users",
        columns_attempted=4,
        matches_by_column={
            "id": [],
            "email": [_make_result("email", rule_name="email_pattern")],
        },
        errors=[
            ColumnError(column_name="geom", error="unsupported type GEOMETRY"),
            ColumnError(column_name="data", error="permission denied"),
        ],
    )


def _make_empty_report() -> TableClassificationReport:
    """A run against a table with zero columns."""
    return TableClassificationReport(
        table="empty_table",
        columns_attempted=0,
        matches_by_column={},
        errors=[],
    )


def _dummy_scan_metadata() -> dict:
    """Return placeholder ScanReport metadata kwargs.

    The render tests are about the rendering of tables/failures, not
    about the metadata fields themselves. Real values for scan_id,
    started_at, completed_at are exercised in the engine tests.
    """
    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    return {
        "scan_id": "test-scan-id",
        "started_at": now,
        "completed_at": now,
        "policy_name": None,
        "target_summary": None,
    }


def _make_clean_scan_result() -> ScanReport:
    """A ScanReport with two successful tables, no failures."""
    return ScanReport(
        **_dummy_scan_metadata(),
        tables=[
            TableClassificationReport(
                table="users",
                columns_attempted=3,
                matches_by_column={
                    "id": [],
                    "email": [_make_result("email", rule_name="email_pattern")],
                    "name": [],
                },
                errors=[],
            ),
            TableClassificationReport(
                table="orders",
                columns_attempted=2,
                matches_by_column={"id": [], "total": []},
                errors=[],
            ),
        ],
        failed_tables=[],
    )


def _make_mixed_scan_result() -> ScanReport:
    """A ScanReport with one success and one failure."""
    return ScanReport(
        **_dummy_scan_metadata(),
        tables=[
            TableClassificationReport(
                table="users",
                columns_attempted=2,
                matches_by_column={
                    "email": [_make_result("email", rule_name="email_pattern")],
                    "id": [],
                },
                errors=[],
            ),
        ],
        failed_tables=[
            FailedTable(name="ghost_table", error="Table not found"),
        ],
    )


def _make_candidates() -> list[TableCandidate]:
    """A sorted list of TableCandidate fixtures (sorted as engine would)."""
    return [
        TableCandidate(table="users", schema_name=None, column_count=5, match_count=3),
        TableCandidate(table="customers", schema_name=None, column_count=6, match_count=2),
        TableCandidate(table="orders", schema_name=None, column_count=8, match_count=1),
        TableCandidate(table="audit_log", schema_name=None, column_count=4, match_count=0),
        TableCandidate(table="config", schema_name=None, column_count=2, match_count=0),
    ]


# ---- Text renderer tests ---------------------------------------------


class TestRenderText:
    """The text renderer produces human-readable output."""

    def test_includes_table_name_in_header(self):
        """The table name appears in the rendered output."""
        output = render_text(_make_clean_report())
        assert "users" in output

    def test_includes_column_count_in_header(self):
        """The columns_attempted count appears."""
        output = render_text(_make_clean_report())
        assert "3 columns" in output

    def test_clean_run_does_not_mention_succeeded_or_failed(self):
        """When errors==[], the header doesn't break out succeeded/failed."""
        output = render_text(_make_clean_report())
        assert "succeeded" not in output
        assert "failed" not in output

    def test_with_errors_shows_succeeded_and_failed_counts(self):
        """When errors exist, header includes both counts."""
        output = render_text(_make_report_with_errors())
        assert "2 succeeded" in output
        assert "2 failed" in output

    def test_matching_column_shows_classification(self):
        """A column with matches displays its classification label."""
        output = render_text(_make_clean_report())
        assert "PII" in output

    def test_matching_column_shows_rule_name(self):
        """A column with matches displays the matched rule name."""
        output = render_text(_make_clean_report())
        assert "email_pattern" in output

    def test_non_matching_column_shows_no_matches(self):
        """Columns with empty match lists display '(no matches)'."""
        output = render_text(_make_clean_report())
        assert "(no matches)" in output

    def test_errors_section_present_when_errors_exist(self):
        """The Errors: section appears when errors are non-empty."""
        output = render_text(_make_report_with_errors())
        assert "Errors:" in output

    def test_errors_section_absent_when_no_errors(self):
        """The Errors: section is omitted on a clean run."""
        output = render_text(_make_clean_report())
        assert "Errors:" not in output

    def test_error_column_name_shown(self):
        """Each error displays its column name."""
        output = render_text(_make_report_with_errors())
        assert "geom" in output
        assert "data" in output

    def test_error_message_shown(self):
        """Each error displays its error message."""
        output = render_text(_make_report_with_errors())
        assert "unsupported type" in output
        assert "permission denied" in output

    def test_empty_report_does_not_crash(self):
        """A report with zero columns renders without errors."""
        # Should not raise
        output = render_text(_make_empty_report())
        assert "empty_table" in output
        assert "0 columns" in output

    def test_multiple_matches_per_column_all_shown(self):
        """A column matching multiple rules displays all rule names."""
        report = TableClassificationReport(
            table="users",
            columns_attempted=1,
            matches_by_column={
                "email": [
                    _make_result("email", rule_name="dict_match"),
                    _make_result("email", rule_name="regex_match"),
                    _make_result("email", rule_name="statistical_match"),
                ],
            },
            errors=[],
        )
        output = render_text(report)
        assert "dict_match" in output
        assert "regex_match" in output
        assert "statistical_match" in output

    def test_rule_names_sorted_in_output(self):
        """Rule names appear sorted (deterministic for testing)."""
        report = TableClassificationReport(
            table="users",
            columns_attempted=1,
            matches_by_column={
                "email": [
                    _make_result("email", rule_name="zebra_rule"),
                    _make_result("email", rule_name="alpha_rule"),
                    _make_result("email", rule_name="middle_rule"),
                ],
            },
            errors=[],
        )
        output = render_text(report)
        # Find positions of each name in the output
        alpha_pos = output.index("alpha_rule")
        middle_pos = output.index("middle_rule")
        zebra_pos = output.index("zebra_rule")
        assert alpha_pos < middle_pos < zebra_pos


# ---- JSON renderer tests ---------------------------------------------


class TestRenderJson:
    """The JSON renderer produces valid, structured JSON output."""

    def test_output_is_valid_json(self):
        """The output is parseable as JSON."""
        output = render_json(_make_clean_report())
        parsed = json.loads(output)  # raises if invalid
        assert isinstance(parsed, dict)

    def test_output_is_indented(self):
        """The output uses indented formatting (not single line)."""
        output = render_json(_make_clean_report())
        # Indented JSON has newlines; compact JSON does not
        assert "\n" in output

    def test_includes_table_field(self):
        """The 'table' field is present."""
        output = render_json(_make_clean_report())
        parsed = json.loads(output)
        assert parsed["table"] == "users"

    def test_includes_columns_attempted(self):
        """The 'columns_attempted' field is present and correct."""
        output = render_json(_make_clean_report())
        parsed = json.loads(output)
        assert parsed["columns_attempted"] == 3

    def test_includes_matches_by_column(self):
        """The 'matches_by_column' field is present and structured correctly."""
        output = render_json(_make_clean_report())
        parsed = json.loads(output)
        assert "matches_by_column" in parsed
        assert isinstance(parsed["matches_by_column"], dict)
        assert "email" in parsed["matches_by_column"]
        assert isinstance(parsed["matches_by_column"]["email"], list)

    def test_match_includes_all_classification_result_fields(self):
        """Each match preserves all four ClassificationResult fields."""
        output = render_json(_make_clean_report())
        parsed = json.loads(output)
        match = parsed["matches_by_column"]["email"][0]
        assert "column_name" in match
        assert "classification" in match
        assert "rule_name" in match
        assert "rule_type" in match

    def test_includes_errors_field(self):
        """The 'errors' field is present (empty list on clean run)."""
        output = render_json(_make_clean_report())
        parsed = json.loads(output)
        assert "errors" in parsed
        assert parsed["errors"] == []

    def test_errors_serialized_correctly(self):
        """Errors are serialized with column_name and error fields."""
        output = render_json(_make_report_with_errors())
        parsed = json.loads(output)
        assert len(parsed["errors"]) == 2
        # ColumnError has column_name + error fields
        errors_by_col = {e["column_name"]: e["error"] for e in parsed["errors"]}
        assert errors_by_col["geom"] == "unsupported type GEOMETRY"
        assert errors_by_col["data"] == "permission denied"

    def test_empty_report_serializes_correctly(self):
        """A report with zero columns produces valid JSON."""
        output = render_json(_make_empty_report())
        parsed = json.loads(output)
        assert parsed["table"] == "empty_table"
        assert parsed["columns_attempted"] == 0
        assert parsed["matches_by_column"] == {}
        assert parsed["errors"] == []


# =====================================================================
# Multi-table progress renderer tests
# =====================================================================


class TestRenderProgressStart:
    """render_progress_start returns the line opener."""

    def test_format(self):
        """Format is 'Scanning <table>... ' with trailing space."""
        assert render_progress_start("users") == "Scanning users... "

    def test_no_trailing_newline(self):
        """The opener has no newline (CLI uses nl=False)."""
        assert "\n" not in render_progress_start("users")


class TestRenderProgressCompleteContinuation:
    """render_progress_complete_continuation appends after the opener."""

    def test_format(self):
        """Format is '<N> columns, <M> classifications'."""
        report = _make_clean_report()
        result = render_progress_complete_continuation(report)
        assert result == "3 columns, 1 classifications"

    def test_zero_classifications(self):
        """Report with no matches shows 0 classifications."""
        report = TableClassificationReport(
            table="x",
            columns_attempted=4,
            matches_by_column={"a": [], "b": [], "c": [], "d": []},
            errors=[],
        )
        assert render_progress_complete_continuation(report) == "4 columns, 0 classifications"

    def test_classifications_count_columns_not_rules(self):
        """A column matched by two rules counts as ONE classification."""
        report = TableClassificationReport(
            table="x",
            columns_attempted=1,
            matches_by_column={
                "email": [
                    _make_result("email", rule_name="rule_a"),
                    _make_result("email", rule_name="rule_b"),
                ],
            },
            errors=[],
        )
        assert render_progress_complete_continuation(report) == "1 columns, 1 classifications"


class TestRenderProgressErrorContinuation:
    """render_progress_error_continuation appends an error message."""

    def test_format(self):
        """Format is 'ERROR (<error>)'."""
        assert render_progress_error_continuation("Table not found") == "ERROR (Table not found)"


# =====================================================================
# Multi-table summary renderer tests
# =====================================================================


class TestRenderScanSummary:
    """render_scan_summary formats the post-scan summary line."""

    def test_clean_run_omits_succeeded_failed(self):
        """A clean run has no '(X succeeded, Y failed)' parenthetical."""
        result = render_scan_summary(_make_clean_scan_result())
        assert "succeeded" not in result
        assert "failed" not in result

    def test_clean_run_includes_table_count(self):
        """Total tables count is shown."""
        result = render_scan_summary(_make_clean_scan_result())
        assert "2 tables" in result

    def test_clean_run_includes_classification_count(self):
        """Total classifications across all tables shown."""
        result = render_scan_summary(_make_clean_scan_result())
        # users has 1 classified column (email); orders has none
        assert "1 classifications total" in result

    def test_mixed_run_shows_succeeded_and_failed(self):
        """A mixed run shows succeeded/failed parenthetical."""
        result = render_scan_summary(_make_mixed_scan_result())
        assert "succeeded" in result
        assert "failed" in result
        assert "2 tables" in result  # 1 success + 1 failure
        assert "1 succeeded" in result
        assert "1 failed" in result

    def test_empty_scan_summary(self):
        """An empty ScanReport summarizes to '0 tables' and 0 classifications."""
        empty = ScanReport(**_dummy_scan_metadata(), tables=[], failed_tables=[])
        result = render_scan_summary(empty)
        assert "0 tables" in result
        assert "0 classifications" in result


# =====================================================================
# Candidates text renderer tests
# =====================================================================


class TestRenderCandidatesText:
    """render_candidates_text produces aligned text."""

    def test_header_with_schema(self):
        """Header reads 'Tables in '<schema>' (<N>):' when schema given."""
        candidates = _make_candidates()
        result = render_candidates_text(candidates, schema="public")
        assert "Tables in 'public' (5):" in result

    def test_header_without_schema(self):
        """Header reads 'Tables in database (<N>):' when schema is None."""
        candidates = _make_candidates()
        result = render_candidates_text(candidates, schema=None)
        assert "Tables in database (5):" in result

    def test_includes_all_table_names(self):
        """Every input candidate appears in output."""
        candidates = _make_candidates()
        result = render_candidates_text(candidates)
        for c in candidates:
            assert c.table in result

    def test_includes_match_counts(self):
        """Each line shows the candidate's match_count."""
        candidates = _make_candidates()
        result = render_candidates_text(candidates)
        # "3 matching" for users (top match count)
        assert "3 matching" in result

    def test_includes_column_counts(self):
        """Each line shows the candidate's column_count."""
        candidates = _make_candidates()
        result = render_candidates_text(candidates)
        # users has 5 cols
        assert "5 cols" in result

    def test_caveat_present(self):
        """The 'column-name rules only' caveat is at the bottom."""
        candidates = _make_candidates()
        result = render_candidates_text(candidates)
        assert "column-name rules only" in result
        assert "classify to be sure" in result

    def test_empty_list_still_shows_caveat(self):
        """Empty input shows header and caveat, no table rows."""
        result = render_candidates_text([], schema="public")
        assert "Tables in 'public' (0):" in result
        assert "column-name rules only" in result

    def test_preserves_engine_sort_order(self):
        """Output order matches input order (engine sorts before render)."""
        candidates = _make_candidates()
        result = render_candidates_text(candidates)
        # users (3 matches) should appear before customers (2 matches)
        users_pos = result.index("users")
        customers_pos = result.index("customers")
        assert users_pos < customers_pos


# =====================================================================
# Candidates JSON renderer tests
# =====================================================================


class TestRenderCandidatesJson:
    """render_candidates_json produces parseable JSON."""

    def test_output_is_valid_json(self):
        """Output parses as JSON."""
        result = render_candidates_json(_make_candidates())
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_has_top_level_keys(self):
        """JSON has schema, total_tables, tables top-level keys."""
        result = render_candidates_json(_make_candidates(), schema="public")
        parsed = json.loads(result)
        assert "schema" in parsed
        assert "total_tables" in parsed
        assert "tables" in parsed

    def test_total_tables_matches_count(self):
        """total_tables equals the candidate count."""
        result = render_candidates_json(_make_candidates())
        parsed = json.loads(result)
        assert parsed["total_tables"] == 5

    def test_schema_field_echoes_input(self):
        """The schema parameter is echoed in the output."""
        result = render_candidates_json(_make_candidates(), schema="public")
        parsed = json.loads(result)
        assert parsed["schema"] == "public"

    def test_schema_field_is_null_for_none(self):
        """schema=None becomes null in JSON."""
        result = render_candidates_json(_make_candidates(), schema=None)
        parsed = json.loads(result)
        assert parsed["schema"] is None

    def test_tables_array_preserves_order(self):
        """tables list preserves engine sort order."""
        result = render_candidates_json(_make_candidates())
        parsed = json.loads(result)
        names = [t["table"] for t in parsed["tables"]]
        # First entry should be 'users' (highest match count)
        assert names[0] == "users"

    def test_table_record_has_all_fields(self):
        """Each table dict contains all TableCandidate fields."""
        result = render_candidates_json(_make_candidates())
        parsed = json.loads(result)
        first = parsed["tables"][0]
        assert "table" in first
        assert "schema_name" in first
        assert "column_count" in first
        assert "match_count" in first

    def test_empty_candidates_produces_valid_json(self):
        """Empty list produces a structurally complete document."""
        result = render_candidates_json([])
        parsed = json.loads(result)
        assert parsed["total_tables"] == 0
        assert parsed["tables"] == []


# =====================================================================
# render_html_report tests
# =====================================================================


def _make_scan_with_metadata(
    *,
    policy_name: str | None = "example",
    target_summary: str | None = "sqlite:///test.db",
) -> ScanReport:
    """A ScanReport with a couple of tables and explicit metadata."""
    meta = _dummy_scan_metadata()
    meta["policy_name"] = policy_name
    meta["target_summary"] = target_summary
    return ScanReport(
        **meta,
        tables=[
            TableClassificationReport(
                table="users",
                columns_attempted=3,
                matches_by_column={
                    "id": [],
                    "email": [_make_result("email", rule_name="email_pattern")],
                    "name": [],
                },
                errors=[],
            ),
            TableClassificationReport(
                table="orders",
                columns_attempted=2,
                matches_by_column={"id": [], "total": []},
                errors=[],
            ),
        ],
        failed_tables=[],
    )


class TestRenderHtmlReportContent:
    """The HTML output contains expected information."""

    def test_returns_string(self):
        """Output is a str (caller is responsible for writing it)."""
        result = render_html_report(_make_scan_with_metadata())
        assert isinstance(result, str)

    def test_is_complete_html_document(self):
        """Output begins with DOCTYPE and ends with </html>."""
        result = render_html_report(_make_scan_with_metadata())
        assert result.startswith("<!DOCTYPE html>")
        assert result.rstrip().endswith("</html>")

    def test_includes_scan_id(self):
        """The scan_id appears in the output (for cross-reference)."""
        result = render_html_report(_make_scan_with_metadata())
        assert "test-scan-id" in result  # from _dummy_scan_metadata

    def test_includes_policy_name(self):
        """The policy name appears in the output."""
        result = render_html_report(_make_scan_with_metadata(policy_name="strict"))
        assert "strict" in result

    def test_omits_policy_name_when_none(self):
        """When policy_name is None, the output says '(not specified)'."""
        result = render_html_report(_make_scan_with_metadata(policy_name=None))
        assert "(not specified)" in result

    def test_includes_target_summary(self):
        """The target summary appears in the output."""
        result = render_html_report(
            _make_scan_with_metadata(target_summary="postgresql://host:5432/db")
        )
        assert "postgresql://host:5432/db" in result

    def test_includes_all_table_names(self):
        """Each successfully classified table appears in the breakdown."""
        result = render_html_report(_make_scan_with_metadata())
        assert "users" in result
        assert "orders" in result

    def test_includes_classification_labels(self):
        """The PII label appears for the email column."""
        result = render_html_report(_make_scan_with_metadata())
        assert ">PII<" in result  # rendered inside a span

    def test_includes_rule_name_for_matches(self):
        """The rule name is shown for matching columns."""
        result = render_html_report(_make_scan_with_metadata())
        assert "email_pattern" in result

    def test_includes_audit_log_path_when_provided(self):
        """audit_log_path argument appears in the footer."""
        result = render_html_report(
            _make_scan_with_metadata(),
            audit_log_path="/tmp/audit.jsonl",
        )
        assert "/tmp/audit.jsonl" in result

    def test_omits_audit_log_path_when_not_provided(self):
        """Without audit_log_path, the footer just shows scan_id."""
        result = render_html_report(_make_scan_with_metadata())
        assert "audit.jsonl" not in result
        # Footer-line about verifying audit chain is also omitted
        assert "dataprism audit verify" not in result


class TestRenderHtmlReportSafety:
    """Sensitive data is not introduced into the output."""

    def test_no_evidence_field_leakage(self):
        """ClassificationResult has no 'evidence' field; output shouldn't claim one."""
        # If a future ClassificationResult adds evidence, this test catches
        # the template silently exposing it.
        result = render_html_report(_make_scan_with_metadata())
        assert "evidence" not in result.lower()

    def test_contains_no_data_values_disclaimer(self):
        """Footer says no sampled values appear in the report."""
        result = render_html_report(_make_scan_with_metadata())
        assert "no sampled data values" in result

    def test_passes_through_target_summary_as_given(self):
        """Renderer doesn't redact; caller is responsible for redaction."""
        # If a caller passes a DSN with a real password, the renderer
        # outputs it verbatim. This is by design and documented.
        result = render_html_report(
            _make_scan_with_metadata(target_summary="postgresql://user:NOT_REDACTED@host:5432/db")
        )
        # We're verifying the renderer doesn't second-guess the caller.
        # In real use, the CLI passes the redacted DSN already.
        assert "NOT_REDACTED" in result


class TestRenderHtmlReportFailures:
    """Failed tables and per-column errors render correctly."""

    def test_failed_table_appears_in_errors_section(self):
        """A FailedTable name and error message appear in the Errors section."""
        meta = _dummy_scan_metadata()
        report = ScanReport(
            **meta,
            tables=[],
            failed_tables=[FailedTable(name="ghost_table", error="Table not found")],
        )
        result = render_html_report(report)
        assert "ghost_table" in result
        assert "Table not found" in result
        assert "Errors" in result

    def test_no_errors_section_when_clean_run(self):
        """A scan with no failures/per-column-errors has no Errors heading."""
        result = render_html_report(_make_scan_with_metadata())
        assert "<h2>Errors</h2>" not in result

    def test_failed_table_count_in_header(self):
        """The header shows the failed-tables count in parentheses."""
        meta = _dummy_scan_metadata()
        report = ScanReport(
            **meta,
            tables=[],
            failed_tables=[
                FailedTable(name="a", error="x"),
                FailedTable(name="b", error="y"),
            ],
        )
        result = render_html_report(report)
        assert "2 failed" in result


class TestRenderHtmlReportEmpty:
    """Edge cases: empty scan, no classifications, etc."""

    def test_empty_scan_renders_without_error(self):
        """A ScanReport with no tables and no failures still renders."""
        meta = _dummy_scan_metadata()
        report = ScanReport(**meta, tables=[], failed_tables=[])
        result = render_html_report(report)
        assert isinstance(result, str)
        assert result.startswith("<!DOCTYPE html>")

    def test_empty_scan_shows_zero_tables_in_summary(self):
        """The executive summary table shows 0 tables scanned."""
        meta = _dummy_scan_metadata()
        report = ScanReport(**meta, tables=[], failed_tables=[])
        result = render_html_report(report)
        # We can't grep for "0" alone (it's everywhere), so look for a
        # known phrase that appears when there are no tables.
        assert "No tables were successfully scanned" in result

    def test_no_classifications_shows_friendly_message(self):
        """A scan where no columns matched shows the empty-category message."""
        meta = _dummy_scan_metadata()
        report = ScanReport(
            **meta,
            tables=[
                TableClassificationReport(
                    table="orders",
                    columns_attempted=2,
                    matches_by_column={"id": [], "total": []},
                    errors=[],
                ),
            ],
            failed_tables=[],
        )
        result = render_html_report(report)
        assert "No columns matched any classification rule" in result


class TestRenderHtmlReportCategories:
    """Color-coded category classes appear when those categories are present."""

    def test_pii_category_uses_pii_class(self):
        """A PII match produces a label-pii CSS class in the output."""
        result = render_html_report(_make_scan_with_metadata())
        assert "label-pii" in result

    def test_financial_category_uses_financial_class(self):
        """A FINANCIAL match produces a label-financial CSS class."""
        meta = _dummy_scan_metadata()
        fin_match = ClassificationResult(
            column_name="account_no",
            classification="FINANCIAL",
            rule_name="account_pattern",
            rule_type="regex",
        )
        report = ScanReport(
            **meta,
            tables=[
                TableClassificationReport(
                    table="accounts",
                    columns_attempted=1,
                    matches_by_column={"account_no": [fin_match]},
                    errors=[],
                ),
            ],
            failed_tables=[],
        )
        result = render_html_report(report)
        assert "label-financial" in result

    def test_unknown_category_falls_back_gracefully(self):
        """An unfamiliar category renders without crashing (uses lowercase class)."""
        meta = _dummy_scan_metadata()
        unusual_match = ClassificationResult(
            column_name="x",
            classification="QUIRKY",
            rule_name="r",
            rule_type="regex",
        )
        report = ScanReport(
            **meta,
            tables=[
                TableClassificationReport(
                    table="t",
                    columns_attempted=1,
                    matches_by_column={"x": [unusual_match]},
                    errors=[],
                ),
            ],
            failed_tables=[],
        )
        result = render_html_report(report)
        assert "label-quirky" in result
        assert "QUIRKY" in result


class TestRenderHtmlReportStructure:
    """Output structure - sections present, well-formed HTML."""

    def test_contains_header_section(self):
        """The header card with timestamps and policy is present."""
        result = render_html_report(_make_scan_with_metadata())
        assert "dataprism Scan Report" in result
        assert "Completed:" in result

    def test_contains_executive_summary(self):
        """The Executive summary section is present."""
        result = render_html_report(_make_scan_with_metadata())
        assert "Executive summary" in result

    def test_contains_per_table_breakdown(self):
        """The Per-table breakdown section is present."""
        result = render_html_report(_make_scan_with_metadata())
        assert "Per-table breakdown" in result

    def test_contains_policy_collapsible(self):
        """The policy section is in a details/summary element."""
        result = render_html_report(_make_scan_with_metadata())
        assert "<details>" in result
        assert "Policy used" in result

    def test_contains_footer_with_scan_id(self):
        """Footer contains the scan_id."""
        result = render_html_report(_make_scan_with_metadata())
        # The footer wraps the scan_id; look for the surrounding context.
        assert "Scan ID:" in result

    def test_duration_appears_in_header(self):
        """The duration (seconds) appears in the header."""
        result = render_html_report(_make_scan_with_metadata())
        # _dummy_scan_metadata uses started == completed, so duration = 0.00
        assert "0.00s" in result

    def test_html_is_self_contained(self):
        """No external CSS or JS references."""
        result = render_html_report(_make_scan_with_metadata())
        # No <link> tags for external CSS
        assert "<link" not in result.lower()
        # No <script> tags
        assert "<script" not in result.lower()
