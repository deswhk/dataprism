"""ClassificationEngine: orchestrates rule evaluation across a policy.

The engine is intentionally thin. Its responsibilities:

- Hold a reference to a loaded ClassificationPolicy and an AuditService.
- For each classify() call, iterate the policy's rules and delegate
  per-rule evaluation to the singledispatch evaluators.
- Collect every match into a list of ClassificationResult.
- Record a CLASSIFICATION_RUN audit event documenting what was
  evaluated and what matched.

Rule-type-specific logic lives in dataprism.classification.evaluators.
To add a new rule type, add a new @evaluate.register function there;
the engine needs no changes.
"""

from __future__ import annotations

from dataprism.audit.events import EventType
from dataprism.audit.service import AuditService
from dataprism.classification.evaluators import evaluate
from dataprism.classification.results import ClassificationResult
from dataprism.policy.models import ClassificationPolicy


class ClassificationEngine:
    """Applies a classification policy to columns.

    Example:
        from pathlib import Path
        from dataprism.audit.service import AuditService
        from dataprism.audit.storage import JsonLinesStorage
        from dataprism.classification.engine import ClassificationEngine
        from dataprism.policy.loader import load_classification_policy

        policy = load_classification_policy(Path("policy.yaml"))
        audit = AuditService(JsonLinesStorage(Path("audit.jsonl")))
        engine = ClassificationEngine(policy, audit)

        results = engine.classify(
            column_name="email_address",
            values=["alice@example.com", "bob@example.com"],
        )
        # results is a list of ClassificationResult, one per matched rule.
    """

    def __init__(
        self,
        policy: ClassificationPolicy,
        audit_service: AuditService,
        actor: str = "classification_engine",
    ) -> None:
        """Construct an engine bound to a policy and audit service.

        Args:
            policy: The loaded classification policy whose rules will
                be evaluated.
            audit_service: Service that receives the CLASSIFICATION_RUN
                event after each classify() call.
            actor: The actor name recorded on audit events. Defaults
                to "classification_engine"; callers should override
                with a meaningful identifier (CLI user, service name)
                when appropriate.
        """
        self._policy = policy
        self._audit = audit_service
        self._actor = actor

    def classify(
        self,
        column_name: str,
        values: list[str] | None = None,
    ) -> list[ClassificationResult]:
        """Evaluate every rule in the policy against the column.

        Returns the list of matches. Every classify() call records one
        CLASSIFICATION_RUN audit event, regardless of whether anything
        matched - the absence of matches is itself useful audit data.

        Args:
            column_name: The name of the column being classified.
            values: Optional sample values from the column. Rules that
                only inspect the column name still work without values;
                rules targeting column values may return False if no
                values are provided.

        Returns:
            A list of ClassificationResult, one per matched rule.
            Empty list if no rule matched. A single column may produce
            multiple results if multiple rules match.
        """
        values = values or []
        results: list[ClassificationResult] = []

        for rule in self._policy.classifiers:
            if evaluate(rule, column_name, values):
                results.append(
                    ClassificationResult(
                        column_name=column_name,
                        classification=rule.classification.value,
                        rule_name=rule.name,
                        rule_type=rule.type,
                    )
                )

        self._audit.record(
            event_type=EventType.CLASSIFICATION_RUN,
            actor=self._actor,
            data={
                "column_name": column_name,
                "rules_evaluated": len(self._policy.classifiers),
                "matches": len(results),
                "matched_rules": [r.rule_name for r in results],
            },
        )
        return results
