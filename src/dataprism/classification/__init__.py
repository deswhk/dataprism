"""Classification subsystem: applies policy rules to columns.

The engine takes a loaded ClassificationPolicy and an AuditService,
then evaluates each rule against a column (its name and optional
sample values). Every match becomes a ClassificationResult; every
classify() call records a CLASSIFICATION_RUN audit event.

Public API:
    ClassificationEngine    - the orchestrator
    ClassificationResult    - the per-match result model
    evaluate                - the singledispatch evaluator function
                              (extension point for custom rule types)
"""
