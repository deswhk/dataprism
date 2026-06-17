"""Tests for ClassificationEngine.

The engine orchestrates rule evaluation across a policy and records
audit events. These tests verify:

- Orchestration: every rule is evaluated; matches are collected.
- Result construction: ClassificationResult fields are populated
  correctly from the rule that matched.
- Multi-match: when multiple rules match a column, all matches are
  returned (Option B from the design decision).
- Audit integration: every classify() call records exactly one
  CLASSIFICATION_RUN event with the right data.
- Edge cases: empty policy, non-matching column, custom actor.

Uses real InMemoryStorage and AuditService rather than mocks, so the
tests verify actual behavior end-to-end.
"""

from dataprism.audit.events import EventType
from dataprism.classification.results import ClassificationResult
from dataprism.policy.models import (
    ClassificationLabel,
    RegexTarget,
    StatisticalRule,
)


class TestClassifyHappyPath:
    """A single matching rule produces a single result."""

    def test_matched_column_returns_one_result(self, make_dict_rule, make_engine):
        rule = make_dict_rule("pii_columns", ["email"])
        engine, _ = make_engine([rule])
        results = engine.classify("email", [])
        assert len(results) == 1

    def test_result_has_expected_fields(self, make_dict_rule, make_engine):
        rule = make_dict_rule("pii_columns", ["email"])
        engine, _ = make_engine([rule])
        results = engine.classify("email", [])
        r = results[0]
        assert isinstance(r, ClassificationResult)
        assert r.column_name == "email"
        assert r.classification == "PII"
        assert r.rule_name == "pii_columns"
        assert r.rule_type == "dictionary"

    def test_classification_is_string_not_enum(self, make_dict_rule, make_engine):
        """ClassificationResult stores classification as string, not enum.
        This is the small concession from the design discussion."""
        rule = make_dict_rule("pii_columns", ["email"])
        engine, _ = make_engine([rule])
        results = engine.classify("email", [])
        assert results[0].classification == "PII"
        assert isinstance(results[0].classification, str)

    def test_non_matching_column_returns_empty_list(self, make_dict_rule, make_engine):
        rule = make_dict_rule("pii_columns", ["email"])
        engine, _ = make_engine([rule])
        results = engine.classify("random_column", [])
        assert results == []


class TestClassifyMultipleRules:
    """Multiple rules in the policy; engine evaluates all of them."""

    def test_only_matching_rules_produce_results(self, make_dict_rule, make_engine):
        engine, _ = make_engine(
            [
                make_dict_rule("pii_dict", ["email"]),
                make_dict_rule("financial_dict", ["credit_card"], ClassificationLabel.FINANCIAL),
            ]
        )
        results = engine.classify("email", [])
        assert len(results) == 1
        assert results[0].rule_name == "pii_dict"

    def test_all_matching_rules_produce_results(self, make_dict_rule, make_regex_rule, make_engine):
        """A column matching multiple rules produces multiple results.
        This is Option B from the design: caller decides on conflicts."""
        engine, _ = make_engine(
            [
                make_dict_rule("dict_match", ["email"]),
                make_regex_rule("regex_match", RegexTarget.COLUMN_NAME, "email"),
            ]
        )
        results = engine.classify("email", [])
        assert len(results) == 2
        rule_names = {r.rule_name for r in results}
        assert rule_names == {"dict_match", "regex_match"}

    def test_results_can_have_different_classifications(
        self, make_dict_rule, make_regex_rule, make_engine
    ):
        """One column matching different rules can produce different labels."""
        engine, _ = make_engine(
            [
                make_dict_rule("as_pii", ["customer_email"], ClassificationLabel.PII),
                make_regex_rule(
                    "as_internal",
                    RegexTarget.COLUMN_NAME,
                    "customer",
                    ClassificationLabel.INTERNAL,
                ),
            ]
        )
        results = engine.classify("customer_email", [])
        labels = {r.classification for r in results}
        assert labels == {"PII", "INTERNAL"}


class TestClassifyMixedRuleTypes:
    """The engine handles all three rule types within one policy."""

    def test_dispatches_correctly_across_rule_types(
        self, make_dict_rule, make_regex_rule, make_engine
    ):
        engine, _ = make_engine(
            [
                make_dict_rule("dict_pii", ["email"]),
                make_regex_rule("regex_pii", RegexTarget.COLUMN_VALUE, r"^\d{3}-\d{2}-\d{4}$"),
                StatisticalRule(
                    type="statistical",
                    name="stat_pii",
                    pattern=r"^\w+@\w+\.\w+$",
                    sample_size=10,
                    min_match_ratio=0.8,
                    classification=ClassificationLabel.PII,
                ),
            ]
        )
        # 'email' column with email values matches dict and statistical
        results = engine.classify(
            "email",
            ["a@b.com", "c@d.com", "e@f.com", "g@h.com", "i@j.com"],
        )
        rule_names = {r.rule_name for r in results}
        assert "dict_pii" in rule_names
        assert "stat_pii" in rule_names


class TestClassifyValuesParameter:
    """Behavior of the values parameter."""

    def test_values_defaults_to_empty_list(self, make_dict_rule, make_engine):
        """Calling classify() without values must not raise."""
        rule = make_dict_rule("name_only", ["email"])
        engine, _ = make_engine([rule])
        results = engine.classify("email")  # no values
        assert len(results) == 1

    def test_explicit_none_values_treated_as_empty(self, make_dict_rule, make_engine):
        rule = make_dict_rule("name_only", ["email"])
        engine, _ = make_engine([rule])
        results = engine.classify("email", None)
        assert len(results) == 1

    def test_value_rules_skipped_without_values(self, make_regex_rule, make_engine):
        """A rule targeting column values returns no match if no values given."""
        rule = make_regex_rule(
            "ssn_value",
            RegexTarget.COLUMN_VALUE,
            r"^\d{3}-\d{2}-\d{4}$",
        )
        engine, _ = make_engine([rule])
        results = engine.classify("some_column", [])
        assert results == []


class TestAuditIntegration:
    """Every classify() call records an audit event."""

    def test_records_exactly_one_event_per_call(self, make_dict_rule, make_engine):
        rule = make_dict_rule("pii_dict", ["email"])
        engine, storage = make_engine([rule])
        engine.classify("email", [])
        events = list(storage.read_all())
        assert len(events) == 1

    def test_event_type_is_classification_run(self, make_dict_rule, make_engine):
        rule = make_dict_rule("pii_dict", ["email"])
        engine, storage = make_engine([rule])
        engine.classify("email", [])
        events = list(storage.read_all())
        assert events[0].event_type == EventType.CLASSIFICATION_RUN

    def test_event_includes_column_name(self, make_dict_rule, make_engine):
        rule = make_dict_rule("pii_dict", ["email"])
        engine, storage = make_engine([rule])
        engine.classify("email", [])
        events = list(storage.read_all())
        assert events[0].data["column_name"] == "email"

    def test_event_records_rules_evaluated_count(self, make_dict_rule, make_engine):
        engine, storage = make_engine(
            [
                make_dict_rule("rule1", ["email"]),
                make_dict_rule("rule2", ["phone"]),
                make_dict_rule("rule3", ["address"]),
            ]
        )
        engine.classify("email", [])
        events = list(storage.read_all())
        assert events[0].data["rules_evaluated"] == 3

    def test_event_records_match_count(self, make_dict_rule, make_engine):
        engine, storage = make_engine(
            [
                make_dict_rule("matches", ["email"]),
                make_dict_rule("does_not_match", ["phone"]),
            ]
        )
        engine.classify("email", [])
        events = list(storage.read_all())
        assert events[0].data["matches"] == 1

    def test_event_records_matched_rule_names(self, make_dict_rule, make_regex_rule, make_engine):
        engine, storage = make_engine(
            [
                make_dict_rule("first_match", ["email"]),
                make_regex_rule("second_match", RegexTarget.COLUMN_NAME, "email"),
                make_dict_rule("non_match", ["phone"]),
            ]
        )
        engine.classify("email", [])
        events = list(storage.read_all())
        matched = events[0].data["matched_rules"]
        assert set(matched) == {"first_match", "second_match"}

    def test_non_matching_classification_still_records_event(self, make_dict_rule, make_engine):
        """Silence is data; the engine records every call regardless of matches."""
        rule = make_dict_rule("pii_dict", ["email"])
        engine, storage = make_engine([rule])
        engine.classify("random_column", [])
        events = list(storage.read_all())
        assert len(events) == 1
        assert events[0].data["matches"] == 0
        assert events[0].data["matched_rules"] == []

    def test_multiple_calls_record_separate_events(self, make_dict_rule, make_engine):
        rule = make_dict_rule("pii_dict", ["email"])
        engine, storage = make_engine([rule])
        engine.classify("email", [])
        engine.classify("phone", [])
        engine.classify("address", [])
        events = list(storage.read_all())
        assert len(events) == 3
        assert [e.data["column_name"] for e in events] == ["email", "phone", "address"]


class TestActorConfiguration:
    """The actor recorded on audit events comes from the engine config."""

    def test_default_actor(self, make_dict_rule, make_engine):
        rule = make_dict_rule("pii_dict", ["email"])
        engine, storage = make_engine([rule])
        engine.classify("email", [])
        events = list(storage.read_all())
        assert events[0].actor == "classification_engine"

    def test_custom_actor(self, make_dict_rule, make_engine):
        rule = make_dict_rule("pii_dict", ["email"])
        engine, storage = make_engine([rule], actor="cli-user")
        engine.classify("email", [])
        events = list(storage.read_all())
        assert events[0].actor == "cli-user"


class TestEmptyPolicy:
    """An empty policy is structurally valid and behaves correctly."""

    def test_empty_policy_returns_no_results(self, make_engine):
        engine, _ = make_engine([])
        results = engine.classify("email", [])
        assert results == []

    def test_empty_policy_still_records_audit_event(self, make_engine):
        """Even with no rules, the engine records that classification ran."""
        engine, storage = make_engine([])
        engine.classify("email", [])
        events = list(storage.read_all())
        assert len(events) == 1
        assert events[0].data["rules_evaluated"] == 0
        assert events[0].data["matches"] == 0
