"""Rule evaluators - the singledispatch core of the classification engine.

Each rule type has an evaluator function registered with
functools.singledispatch. Calling evaluate(rule, ...) dispatches to
the right implementation based on the rule's runtime type.

To add a new rule type:
    1. Add the new Pydantic model to dataprism.policy.models and the
       discriminated union there.
    2. Add a new @evaluate.register function in this file.
    3. Add tests for the new evaluator in tests/classification/test_evaluators.py.

No other code changes are needed; the engine picks up the new evaluator
automatically because it just calls evaluate(rule, ...).
"""

from __future__ import annotations

import re
from functools import singledispatch

from dataprism.policy.models import (
    ClassificationRule,
    DictionaryMatchMode,
    DictionaryRule,
    RegexRule,
    RegexTarget,
    StatisticalRule,
)


def _normalize(s: str) -> str:
    """Lowercase a string and strip common separators.

    Used by dictionary matching to handle naming variations:
    "Email", "email", "e_mail", "e-mail", "e mail" all normalize
    to "email".
    """
    return s.lower().replace("_", "").replace("-", "").replace(" ", "")


@singledispatch
def evaluate(
    rule: ClassificationRule,
    column_name: str,
    values: list[str],
) -> bool:
    """Evaluate a rule against a column.

    This is the public entry point. The actual logic lives in the
    type-specific implementations registered below.

    Args:
        rule: A classification rule (RegexRule, DictionaryRule, or
            StatisticalRule).
        column_name: The name of the column being evaluated.
        values: Sample values from the column. May be empty for rules
            that only look at the column name.

    Returns:
        True if the rule matches, False otherwise.

    Raises:
        NotImplementedError: If no evaluator is registered for the
            rule's type. This indicates a missing @register decorator.
    """
    raise NotImplementedError(f"No evaluator registered for rule type: {type(rule).__name__}")


@evaluate.register
def _evaluate_regex(
    rule: RegexRule,
    column_name: str,
    values: list[str],
) -> bool:
    """Evaluate a regex rule against either the column name or values.

    For target=COLUMN_NAME: match the pattern against the column name once.
    For target=COLUMN_VALUE: every sample value must match. Empty values
        list returns False (no values means nothing to verify, and we
        don't want to claim a positive match without evidence).
    """
    pattern = re.compile(rule.pattern)
    if rule.target == RegexTarget.COLUMN_NAME:
        return bool(pattern.search(column_name))
    if not values:
        return False
    return all(bool(pattern.search(v)) for v in values)


@evaluate.register
def _evaluate_dictionary(
    rule: DictionaryRule,
    column_name: str,
    values: list[str],  # noqa: ARG001 - kept for signature consistency
) -> bool:
    """Evaluate a dictionary rule against the column name.

    Dictionary rules match against the column name only; values are
    accepted in the signature for uniform dispatch but ignored here.

    Three modes:
        EXACT: case-sensitive exact match.
        EXACT_NORMALIZED: normalized exact match (default). Handles
            common name variation (Email, email, e_mail, e-mail).
        CONTAINS_NORMALIZED: substring match after normalization.
            Use with care - prone to false positives like "email"
            matching "emailable".
    """
    if rule.match_mode == DictionaryMatchMode.EXACT:
        return column_name in rule.values

    normalized_column = _normalize(column_name)
    normalized_values = [_normalize(v) for v in rule.values]

    if rule.match_mode == DictionaryMatchMode.EXACT_NORMALIZED:
        return normalized_column in normalized_values

    # CONTAINS_NORMALIZED
    return any(v in normalized_column for v in normalized_values)


@evaluate.register
def _evaluate_statistical(
    rule: StatisticalRule,
    column_name: str,  # noqa: ARG001 - kept for signature consistency
    values: list[str],
) -> bool:
    """Evaluate a statistical rule by sampling values.

    Samples up to rule.sample_size values, applies the regex pattern
    to each, and returns True if the match ratio meets or exceeds
    rule.min_match_ratio.

    Empty values returns False - no sample means no evidence.
    """
    if not values:
        return False
    sample = values[: rule.sample_size]
    pattern = re.compile(rule.pattern)
    matches = sum(1 for v in sample if pattern.search(v))
    ratio = matches / len(sample)
    return ratio >= rule.min_match_ratio
