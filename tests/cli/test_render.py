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

from dataprism.classification.results import ClassificationResult
from dataprism.classification.table import ColumnError, TableClassificationReport
from dataprism.cli.render import render_json, render_text

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
