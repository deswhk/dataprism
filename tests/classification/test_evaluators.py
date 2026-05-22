"""Tests for the rule evaluator functions.

These tests call the singledispatch evaluate() function directly with
constructed rules - no engine, no policy, no audit. Pure function tests.

Each rule type gets its own test class. Within a class, tests cover:
- Happy paths (rule matches)
- Negative paths (rule doesn't match)
- Edge cases (empty values, boundary conditions)
- Mode/target variations specific to that rule type
"""

import pytest

from dataprism.classification.evaluators import _normalize, evaluate
from dataprism.policy.models import (
    ClassificationLabel,
    DictionaryMatchMode,
    DictionaryRule,
    RegexRule,
    RegexTarget,
    StatisticalRule,
)

# Shared classification label for tests where the specific label
# doesn't matter - we're testing the matching logic, not the labeling.
PII = ClassificationLabel.PII


class TestNormalize:
    """The _normalize helper used by dictionary matching."""

    def test_lowercases(self):
        assert _normalize("EMAIL") == "email"

    def test_strips_underscores(self):
        assert _normalize("email_address") == "emailaddress"

    def test_strips_hyphens(self):
        assert _normalize("e-mail") == "email"

    def test_strips_spaces(self):
        assert _normalize("e mail") == "email"

    def test_combines_all(self):
        assert _normalize("Email_Address") == "emailaddress"
        assert _normalize("E-Mail Address") == "emailaddress"

    def test_idempotent(self):
        """Normalizing an already-normalized string is a no-op."""
        assert _normalize(_normalize("Email_Address")) == _normalize("Email_Address")


class TestEvaluateRegexColumnName:
    """RegexRule with target=COLUMN_NAME."""

    def _rule(self, pattern: str) -> RegexRule:
        return RegexRule(
            type="regex",
            name="test",
            target=RegexTarget.COLUMN_NAME,
            pattern=pattern,
            classification=PII,
        )

    def test_matches_when_pattern_matches_column_name(self):
        assert evaluate(self._rule(r"^email"), "email_address", []) is True

    def test_does_not_match_when_pattern_does_not_match(self):
        assert evaluate(self._rule(r"^email"), "phone", []) is False

    def test_uses_search_not_full_match(self):
        """Patterns find substrings anywhere in the name by default."""
        assert evaluate(self._rule("email"), "user_email_field", []) is True

    def test_case_sensitive_by_default(self):
        """Without (?i), patterns are case-sensitive."""
        assert evaluate(self._rule("Email"), "email_address", []) is False

    def test_case_insensitive_with_flag(self):
        """The (?i) flag makes the regex case-insensitive."""
        assert evaluate(self._rule("(?i)email"), "EMAIL_ADDRESS", []) is True

    def test_ignores_values_for_column_name_target(self):
        """When target=COLUMN_NAME, values are not consulted."""
        rule = self._rule("email")
        assert evaluate(rule, "email_field", ["totally", "irrelevant"]) is True
        assert evaluate(rule, "phone_field", ["totally", "irrelevant"]) is False


class TestEvaluateRegexColumnValue:
    """RegexRule with target=COLUMN_VALUE."""

    def _rule(self, pattern: str) -> RegexRule:
        return RegexRule(
            type="regex",
            name="test",
            target=RegexTarget.COLUMN_VALUE,
            pattern=pattern,
            classification=PII,
        )

    def test_matches_when_all_values_match(self):
        rule = self._rule(r"^\d{3}-\d{2}-\d{4}$")
        values = ["123-45-6789", "111-22-3333", "999-88-7777"]
        assert evaluate(rule, "col", values) is True

    def test_does_not_match_when_any_value_fails(self):
        """One non-matching value defeats the whole rule."""
        rule = self._rule(r"^\d{3}-\d{2}-\d{4}$")
        values = ["123-45-6789", "not-an-ssn", "999-88-7777"]
        assert evaluate(rule, "col", values) is False

    def test_empty_values_returns_false(self):
        """No values means no evidence; cannot claim a positive match."""
        rule = self._rule(r"^\d{3}-\d{2}-\d{4}$")
        assert evaluate(rule, "col", []) is False

    def test_ignores_column_name_for_value_target(self):
        """The column name is not consulted when target=COLUMN_VALUE."""
        rule = self._rule(r"^\d{3}-\d{2}-\d{4}$")
        values = ["123-45-6789"]
        assert evaluate(rule, "totally_random_name", values) is True


class TestEvaluateDictionaryExact:
    """DictionaryRule with match_mode=EXACT."""

    def _rule(self, values: list[str]) -> DictionaryRule:
        return DictionaryRule(
            type="dictionary",
            name="test",
            values=values,
            match_mode=DictionaryMatchMode.EXACT,
            classification=PII,
        )

    def test_exact_match(self):
        assert evaluate(self._rule(["email"]), "email", []) is True

    def test_case_sensitive(self):
        """EXACT is byte-for-byte; case differences don't match."""
        assert evaluate(self._rule(["email"]), "Email", []) is False

    def test_no_normalization(self):
        """EXACT does no normalization; separators matter."""
        assert evaluate(self._rule(["email"]), "e_mail", []) is False

    def test_matches_any_value_in_list(self):
        rule = self._rule(["email", "phone", "address"])
        assert evaluate(rule, "phone", []) is True

    def test_does_not_match_when_absent(self):
        assert evaluate(self._rule(["email"]), "phone", []) is False


class TestEvaluateDictionaryExactNormalized:
    """DictionaryRule with match_mode=EXACT_NORMALIZED (the default)."""

    def _rule(self, values: list[str]) -> DictionaryRule:
        return DictionaryRule(
            type="dictionary",
            name="test",
            values=values,
            match_mode=DictionaryMatchMode.EXACT_NORMALIZED,
            classification=PII,
        )

    def test_case_insensitive_match(self):
        assert evaluate(self._rule(["email"]), "Email", []) is True
        assert evaluate(self._rule(["email"]), "EMAIL", []) is True

    def test_separator_insensitive_match(self):
        assert evaluate(self._rule(["email_address"]), "email-address", []) is True
        assert evaluate(self._rule(["email_address"]), "Email Address", []) is True

    def test_combined_case_and_separator(self):
        assert evaluate(self._rule(["email_address"]), "Email-Address", []) is True

    def test_does_not_match_different_concept(self):
        """Normalization does NOT make 'email' match 'email_address'."""
        assert evaluate(self._rule(["email"]), "email_address", []) is False


class TestEvaluateDictionaryContainsNormalized:
    """DictionaryRule with match_mode=CONTAINS_NORMALIZED."""

    def _rule(self, values: list[str]) -> DictionaryRule:
        return DictionaryRule(
            type="dictionary",
            name="test",
            values=values,
            match_mode=DictionaryMatchMode.CONTAINS_NORMALIZED,
            classification=PII,
        )

    def test_substring_match(self):
        """The dictionary value appears as a substring of the normalized name."""
        assert evaluate(self._rule(["email"]), "Email_Address_Field", []) is True

    def test_substring_match_with_separators(self):
        assert evaluate(self._rule(["ssn"]), "customer_ssn_number", []) is True

    def test_no_substring_returns_false(self):
        assert evaluate(self._rule(["email"]), "phone_number", []) is False

    def test_demonstrates_false_positive_risk(self):
        """Documenting the known false-positive case: 'email' matches 'emailable'.
        This is documented behavior, not a bug - operators must choose values
        carefully for CONTAINS_NORMALIZED mode."""
        assert evaluate(self._rule(["email"]), "emailable_flag", []) is True


class TestEvaluateStatistical:
    """StatisticalRule."""

    def _rule(
        self,
        pattern: str,
        sample_size: int = 100,
        min_match_ratio: float = 0.9,
    ) -> StatisticalRule:
        return StatisticalRule(
            type="statistical",
            name="test",
            pattern=pattern,
            sample_size=sample_size,
            min_match_ratio=min_match_ratio,
            classification=PII,
        )

    def test_full_match_passes_threshold(self):
        rule = self._rule(r"^\w+@\w+\.\w+$", min_match_ratio=0.9)
        values = ["a@b.co", "c@d.co", "e@f.co", "g@h.co", "i@j.co"]
        assert evaluate(rule, "col", values) is True

    def test_zero_match_fails(self):
        rule = self._rule(r"^\w+@\w+\.\w+$")
        values = ["not", "emails", "at", "all"]
        assert evaluate(rule, "col", values) is False

    def test_exact_threshold_passes(self):
        """If ratio EQUALS min_match_ratio, the rule passes (>=, not >)."""
        rule = self._rule(r"match", min_match_ratio=0.5)
        values = ["match", "match", "no", "no"]  # 2/4 = 0.5
        assert evaluate(rule, "col", values) is True

    def test_below_threshold_fails(self):
        rule = self._rule(r"match", min_match_ratio=0.6)
        values = ["match", "match", "no", "no"]  # 2/4 = 0.5, below 0.6
        assert evaluate(rule, "col", values) is False

    def test_empty_values_returns_false(self):
        rule = self._rule(r"anything")
        assert evaluate(rule, "col", []) is False

    def test_respects_sample_size_limit(self):
        """Only the first sample_size values are considered."""
        rule = self._rule(r"good", sample_size=3, min_match_ratio=1.0)
        # The first 3 are all "good"; subsequent values are ignored.
        values = ["good", "good", "good", "bad", "bad", "bad"]
        assert evaluate(rule, "col", values) is True

    def test_ignores_column_name(self):
        """Statistical rules look only at values."""
        rule = self._rule(r"^\d+$")
        values = ["123", "456", "789"]
        assert evaluate(rule, "irrelevant_name", values) is True


class TestEvaluateDispatch:
    """The singledispatch wiring itself."""

    def test_registry_has_all_three_rule_types(self):
        """All three concrete rule types must be registered."""
        registered_types = set(evaluate.registry.keys())
        assert RegexRule in registered_types
        assert DictionaryRule in registered_types
        assert StatisticalRule in registered_types

    def test_unregistered_type_raises(self):
        """Calling evaluate with a non-rule object raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            evaluate("not a rule", "col", [])  # type: ignore[arg-type]

    def test_dispatch_picks_correct_implementation(self):
        """evaluate.dispatch(type) returns the registered evaluator function."""
        # We don't compare specific function references (implementation detail),
        # but we verify dispatch returns DIFFERENT functions for different types.
        regex_fn = evaluate.dispatch(RegexRule)
        dict_fn = evaluate.dispatch(DictionaryRule)
        stat_fn = evaluate.dispatch(StatisticalRule)
        assert regex_fn is not dict_fn
        assert dict_fn is not stat_fn
        assert regex_fn is not stat_fn
