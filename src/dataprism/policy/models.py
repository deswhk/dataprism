"""Pydantic models for classification policies.

Defines the schema that YAML policy files are validated against. The
shape of valid YAML is determined entirely by these models - if a file
parses and validates, you can trust its structure.

The model design uses a discriminated union for rule types: each rule
declares its 'type' field, and Pydantic dispatches to the matching
model (RegexRule, DictionaryRule, StatisticalRule) based on that value.
This gives strong validation: a rule with type='regex' must have the
RegexRule fields, not the DictionaryRule fields.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ClassificationLabel(StrEnum):
    """The set of allowed classification labels.

    Mapping to common regulatory frameworks:
        PII        - Personally Identifiable Information (GDPR, CCPA)
        PHI        - Protected Health Information (HIPAA)
        FINANCIAL  - Credit cards, bank accounts (PCI-DSS)
        CREDENTIAL - Passwords, API keys, secrets
        PUBLIC     - Explicitly non-sensitive
        INTERNAL   - Sensitive to the organization but not regulated
    """

    PII = "PII"
    PHI = "PHI"
    FINANCIAL = "FINANCIAL"
    CREDENTIAL = "CREDENTIAL"
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"


class DictionaryMatchMode(StrEnum):
    """How a DictionaryRule matches column names against its values.

    EXACT                 - case-sensitive exact match
    EXACT_NORMALIZED      - lowercase + strip [_-. ] before comparing
    CONTAINS_NORMALIZED   - normalized substring match (use with care)

    See dataprism docs for guidance on choosing a mode. The default is
    EXACT_NORMALIZED which handles the common case of inconsistent
    naming (Email, email, e_mail, e-mail all match a single entry).
    """

    EXACT = "exact"
    EXACT_NORMALIZED = "exact_normalized"
    CONTAINS_NORMALIZED = "contains_normalized"


class RegexTarget(StrEnum):
    """Which part of a column a RegexRule applies its pattern to.

    COLUMN_NAME   - match the regex against the column's name
    COLUMN_VALUE  - match the regex against each value in the column
                    (deterministic: every value must match)

    For probabilistic value matching (some-but-not-all values match),
    use StatisticalRule instead, which has explicit sample size and
    match ratio parameters.
    """

    COLUMN_NAME = "column_name"
    COLUMN_VALUE = "column_value"


class RegexRule(BaseModel):
    """A regex-based classification rule.

    Applies the pattern to either the column name or each column value
    depending on the target field. For value-targeted rules, the engine
    requires every sampled value to match - for partial matching, use
    StatisticalRule instead.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["regex"]
    name: str
    target: RegexTarget
    pattern: str
    classification: ClassificationLabel


class DictionaryRule(BaseModel):
    """A dictionary-based rule that matches column names against a list.

    Always targets the column name (value-based dictionary matching is
    not supported in v1 - for value matching see RegexRule and
    StatisticalRule).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["dictionary"]
    name: str
    values: list[str]
    classification: ClassificationLabel
    match_mode: DictionaryMatchMode = DictionaryMatchMode.EXACT_NORMALIZED


class StatisticalRule(BaseModel):
    """A rule that samples column values and classifies by match ratio.

    Samples up to sample_size values from the column, applies the
    regex pattern to each, and classifies the column if the fraction
    of matches meets or exceeds min_match_ratio.

    Useful for catching mislabeled columns: a column named 'field_3'
    containing emails will be classified as PII by this rule even
    though no name-based rule could detect it.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["statistical"]
    name: str
    pattern: str
    classification: ClassificationLabel
    sample_size: int = Field(default=1000, ge=1)
    min_match_ratio: float = Field(default=0.95, ge=0.0, le=1.0)


# Discriminated union: Pydantic looks at the 'type' field of each rule
# in YAML and dispatches to the matching model. This gives strong
# validation - a rule with type='regex' must satisfy RegexRule's shape,
# not DictionaryRule's, even though they live in the same list.
ClassificationRule = Annotated[
    RegexRule | DictionaryRule | StatisticalRule,
    Field(discriminator="type"),
]


class ClassificationPolicy(BaseModel):
    """Top-level model for a classification policy file.

    A policy file declares a version (for forward compatibility) and
    a list of classifiers. Each classifier is one rule of any
    supported type.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    classifiers: list[ClassificationRule]
