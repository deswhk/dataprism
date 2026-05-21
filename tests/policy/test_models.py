"""Tests for the policy Pydantic models.

These tests construct rules and policies directly in Python (no YAML).
They cover:

- Enum behavior (StrEnum members serialize as strings)
- Per-rule-type construction and validation
- Discriminated union dispatch (the right model is chosen by type)
- Strict mode (extra='forbid') rejection of unknown fields
- Default values applied where omitted
- Numeric constraints (ge, le on sample_size, min_match_ratio)
"""

import pytest
from pydantic import ValidationError

from dataprism.policy.models import (
    ClassificationLabel,
    ClassificationPolicy,
    DictionaryMatchMode,
    DictionaryRule,
    RegexRule,
    RegexTarget,
    StatisticalRule,
)


class TestClassificationLabel:
    """The ClassificationLabel enum behaves as a string enum."""

    def test_label_values_are_strings(self):
        assert ClassificationLabel.PII == "PII"
        assert ClassificationLabel.PHI == "PHI"
        assert ClassificationLabel.FINANCIAL == "FINANCIAL"

    def test_label_set_membership(self):
        """Useful for filtering events or rules by classification."""
        sensitive = {ClassificationLabel.PII, ClassificationLabel.PHI}
        assert ClassificationLabel.PII in sensitive
        assert ClassificationLabel.PUBLIC not in sensitive


class TestDictionaryMatchMode:
    """The DictionaryMatchMode enum exposes three matching strategies."""

    def test_match_mode_values(self):
        assert DictionaryMatchMode.EXACT == "exact"
        assert DictionaryMatchMode.EXACT_NORMALIZED == "exact_normalized"
        assert DictionaryMatchMode.CONTAINS_NORMALIZED == "contains_normalized"


class TestRegexTarget:
    """The RegexTarget enum exposes two targets for regex rules."""

    def test_regex_target_values(self):
        assert RegexTarget.COLUMN_NAME == "column_name"
        assert RegexTarget.COLUMN_VALUE == "column_value"


class TestRegexRule:
    """RegexRule construction and validation."""

    def test_minimal_construction(self):
        rule = RegexRule(
            type="regex",
            name="ssn",
            target=RegexTarget.COLUMN_VALUE,
            pattern=r"^\d{3}-\d{2}-\d{4}$",
            classification=ClassificationLabel.PII,
        )
        assert rule.name == "ssn"
        assert rule.target == RegexTarget.COLUMN_VALUE
        assert rule.classification == ClassificationLabel.PII

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            RegexRule(
                type="regex",
                name="x",
                target=RegexTarget.COLUMN_VALUE,
                pattern="x",
                classification=ClassificationLabel.PII,
                extra_field="not allowed",
            )

    def test_missing_pattern_rejected(self):
        with pytest.raises(ValidationError):
            RegexRule(
                type="regex",
                name="x",
                target=RegexTarget.COLUMN_VALUE,
                classification=ClassificationLabel.PII,
            )

    def test_missing_target_rejected(self):
        with pytest.raises(ValidationError):
            RegexRule(
                type="regex",
                name="x",
                pattern="x",
                classification=ClassificationLabel.PII,
            )


class TestDictionaryRule:
    """DictionaryRule construction, validation, and defaults."""

    def test_minimal_construction(self):
        rule = DictionaryRule(
            type="dictionary",
            name="pii_columns",
            values=["email", "phone"],
            classification=ClassificationLabel.PII,
        )
        assert rule.values == ["email", "phone"]

    def test_match_mode_defaults_to_exact_normalized(self):
        """When match_mode is omitted, the default is EXACT_NORMALIZED."""
        rule = DictionaryRule(
            type="dictionary",
            name="pii_columns",
            values=["email"],
            classification=ClassificationLabel.PII,
        )
        assert rule.match_mode == DictionaryMatchMode.EXACT_NORMALIZED

    def test_match_mode_can_be_overridden(self):
        rule = DictionaryRule(
            type="dictionary",
            name="pii_columns",
            values=["email"],
            classification=ClassificationLabel.PII,
            match_mode=DictionaryMatchMode.CONTAINS_NORMALIZED,
        )
        assert rule.match_mode == DictionaryMatchMode.CONTAINS_NORMALIZED

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            DictionaryRule(
                type="dictionary",
                name="x",
                values=["email"],
                classification=ClassificationLabel.PII,
                target="column_name",  # not a field on DictionaryRule
            )

    def test_empty_values_list_allowed(self):
        """An empty values list is structurally valid (engine semantics
        may treat it as a no-op rule)."""
        rule = DictionaryRule(
            type="dictionary",
            name="empty",
            values=[],
            classification=ClassificationLabel.PII,
        )
        assert rule.values == []


class TestStatisticalRule:
    """StatisticalRule construction, defaults, and numeric constraints."""

    def test_minimal_construction(self):
        rule = StatisticalRule(
            type="statistical",
            name="email_sampling",
            pattern=r"^[\w.-]+@[\w.-]+\.\w+$",
            classification=ClassificationLabel.PII,
        )
        assert rule.sample_size == 1000
        assert rule.min_match_ratio == 0.95

    def test_overriding_defaults(self):
        rule = StatisticalRule(
            type="statistical",
            name="x",
            pattern="x",
            sample_size=500,
            min_match_ratio=0.8,
            classification=ClassificationLabel.PII,
        )
        assert rule.sample_size == 500
        assert rule.min_match_ratio == 0.8

    def test_sample_size_must_be_positive(self):
        with pytest.raises(ValidationError):
            StatisticalRule(
                type="statistical",
                name="x",
                pattern="x",
                sample_size=0,
                classification=ClassificationLabel.PII,
            )

    def test_min_match_ratio_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            StatisticalRule(
                type="statistical",
                name="x",
                pattern="x",
                min_match_ratio=-0.1,
                classification=ClassificationLabel.PII,
            )

    def test_min_match_ratio_above_one_rejected(self):
        with pytest.raises(ValidationError):
            StatisticalRule(
                type="statistical",
                name="x",
                pattern="x",
                min_match_ratio=1.5,
                classification=ClassificationLabel.PII,
            )

    def test_min_match_ratio_boundaries_accepted(self):
        """0.0 and 1.0 are the boundary values; both must be allowed."""
        r0 = StatisticalRule(
            type="statistical",
            name="x",
            pattern="x",
            min_match_ratio=0.0,
            classification=ClassificationLabel.PII,
        )
        r1 = StatisticalRule(
            type="statistical",
            name="x",
            pattern="x",
            min_match_ratio=1.0,
            classification=ClassificationLabel.PII,
        )
        assert r0.min_match_ratio == 0.0
        assert r1.min_match_ratio == 1.0


class TestClassificationPolicy:
    """Top-level ClassificationPolicy construction and discriminator dispatch."""

    def test_minimal_policy(self):
        rule = DictionaryRule(
            type="dictionary",
            name="x",
            values=["email"],
            classification=ClassificationLabel.PII,
        )
        policy = ClassificationPolicy(version=1, classifiers=[rule])
        assert policy.version == 1
        assert len(policy.classifiers) == 1

    def test_mixed_rule_types_in_one_policy(self):
        """A single policy can contain different rule types."""
        rules = [
            DictionaryRule(
                type="dictionary",
                name="d",
                values=["email"],
                classification=ClassificationLabel.PII,
            ),
            RegexRule(
                type="regex",
                name="r",
                target=RegexTarget.COLUMN_VALUE,
                pattern="x",
                classification=ClassificationLabel.PII,
            ),
            StatisticalRule(
                type="statistical",
                name="s",
                pattern="x",
                classification=ClassificationLabel.PII,
            ),
        ]
        policy = ClassificationPolicy(version=1, classifiers=rules)
        assert len(policy.classifiers) == 3

    def test_version_must_be_at_least_one(self):
        with pytest.raises(ValidationError):
            ClassificationPolicy(version=0, classifiers=[])

    def test_unknown_top_level_field_rejected(self):
        with pytest.raises(ValidationError):
            ClassificationPolicy(
                version=1,
                classifiers=[],
                extra_top_level="not allowed",
            )

    def test_empty_classifiers_list_allowed(self):
        """A policy with zero rules is structurally valid (engine may
        treat it as 'nothing to classify')."""
        policy = ClassificationPolicy(version=1, classifiers=[])
        assert policy.classifiers == []


class TestDiscriminatorDispatch:
    """The discriminated union picks the right model based on the 'type' field.

    These tests use model_validate (parsing from dicts) because that's how
    YAML data enters the model. Direct construction always knows the type.
    """

    def test_dict_with_type_regex_becomes_regex_rule(self):
        data = {
            "version": 1,
            "classifiers": [
                {
                    "type": "regex",
                    "name": "x",
                    "target": "column_value",
                    "pattern": "y",
                    "classification": "PII",
                }
            ],
        }
        policy = ClassificationPolicy.model_validate(data)
        assert isinstance(policy.classifiers[0], RegexRule)

    def test_dict_with_type_dictionary_becomes_dictionary_rule(self):
        data = {
            "version": 1,
            "classifiers": [
                {
                    "type": "dictionary",
                    "name": "x",
                    "values": ["email"],
                    "classification": "PII",
                }
            ],
        }
        policy = ClassificationPolicy.model_validate(data)
        assert isinstance(policy.classifiers[0], DictionaryRule)

    def test_dict_with_type_statistical_becomes_statistical_rule(self):
        data = {
            "version": 1,
            "classifiers": [
                {
                    "type": "statistical",
                    "name": "x",
                    "pattern": "y",
                    "classification": "PII",
                }
            ],
        }
        policy = ClassificationPolicy.model_validate(data)
        assert isinstance(policy.classifiers[0], StatisticalRule)

    def test_unknown_type_rejected(self):
        data = {
            "version": 1,
            "classifiers": [
                {
                    "type": "nonexistent",
                    "name": "x",
                    "classification": "PII",
                }
            ],
        }
        with pytest.raises(ValidationError):
            ClassificationPolicy.model_validate(data)

    def test_missing_discriminator_rejected(self):
        """A rule without a 'type' field cannot be dispatched."""
        data = {
            "version": 1,
            "classifiers": [
                {
                    "name": "x",
                    "values": ["email"],
                    "classification": "PII",
                }
            ],
        }
        with pytest.raises(ValidationError):
            ClassificationPolicy.model_validate(data)
