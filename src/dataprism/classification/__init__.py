"""Classification subsystem: applies policy rules to columns.

The engine takes a loaded ClassificationPolicy and an AuditService,
then evaluates each rule against a column (its name and optional
sample values). Every match becomes a ClassificationResult; every
classify() call records a CLASSIFICATION_RUN audit event.

The high-level classify_table() function combines a DatabaseAdapter
with the engine to classify every column in a table - including audit
bookend events (TABLE_CLASSIFICATION_STARTED/COMPLETED) and per-column
error collection in a TableClassificationReport.

Public API:
    ClassificationEngine        - the per-column orchestrator
    ClassificationResult        - the per-match result model
    evaluate                    - the singledispatch evaluator function
                                  (extension point for custom rule types)
    classify_table              - high-level "classify every column" function
    TableClassificationReport   - structured result from classify_table
    ColumnError                 - per-column failure record
"""
