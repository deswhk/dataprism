"""Result model for classification matches.

A ClassificationResult is produced whenever a rule matches a column.
The engine returns a list of these from each classify() call.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ClassificationResult(BaseModel):
    """A single rule match against a column.

    Returned by ClassificationEngine.classify() - one result per
    matched rule. A column that matches multiple rules produces
    multiple results in the same list.

    Attributes:
        column_name: The name of the column that matched.
        classification: The label assigned by the matched rule.
            Comes directly from the rule's classification field.
        rule_name: The name of the matched rule, for audit and
            debugging.
        rule_type: The type discriminator of the matched rule
            (e.g. "regex", "dictionary", "statistical"). Useful for
            consumers that want to filter by rule kind without
            re-querying the policy.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    column_name: str
    classification: str
    rule_name: str
    rule_type: str
