"""Policy subsystem: declarative governance rules.

Policies are YAML files validated against Pydantic models. They declare
WHAT the governance intent is (which patterns are PII, which checks to
run on which columns) separately from HOW the engine implements it.

Public API:
    ClassificationPolicy    - the top-level policy file model
    ClassificationRule      - discriminated union of rule types
    RegexRule               - regex-based rules (column name or value)
    DictionaryRule          - dictionary-based rules (column name match)
    StatisticalRule         - statistical sampling rules (column values)
    ClassificationLabel     - the set of valid classification labels
    DictionaryMatchMode     - matching strategy for dictionary rules
    RegexTarget             - whether a regex rule targets names or values
    load_classification_policy             - pure loader
    load_and_audit_classification_policy   - loader with audit wrapping
    PolicyError, PolicyLoadError, PolicyValidationError - exceptions
"""
