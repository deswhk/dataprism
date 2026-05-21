"""Tests for the policy YAML loader and its audit-wrapping companion.

Two test classes:

- TestLoadClassificationPolicy: the pure loader. Uses fixture files in
  tests/fixtures/policies/ to verify happy paths and each of the
  failure modes documented in policy/errors.py.

- TestLoadAndAuditClassificationPolicy: the audit-wrapping loader.
  Uses InMemoryStorage to verify the right audit events are written
  on success and failure.
"""

from pathlib import Path

import pytest

from dataprism.audit.events import EventType
from dataprism.audit.service import AuditService
from dataprism.audit.storage import InMemoryStorage
from dataprism.policy.errors import PolicyLoadError, PolicyValidationError
from dataprism.policy.loader import (
    load_and_audit_classification_policy,
    load_classification_policy,
)
from dataprism.policy.models import (
    ClassificationLabel,
    DictionaryMatchMode,
    DictionaryRule,
    RegexRule,
    StatisticalRule,
)

# Fixture files live in tests/fixtures/policies/.
FIXTURES = Path(__file__).parent.parent / "fixtures" / "policies"


class TestLoadClassificationPolicy:
    """Happy paths and each failure mode of the pure loader."""

    # ---- Happy paths ------------------------------------------------

    def test_loads_valid_classification_fixture(self):
        """A full-featured valid policy loads with all expected rules."""
        policy = load_classification_policy(FIXTURES / "valid_classification.yaml")
        assert policy.version == 1
        assert len(policy.classifiers) == 4

    def test_loads_minimal_fixture(self):
        """A minimal valid policy loads successfully."""
        policy = load_classification_policy(FIXTURES / "valid_minimal.yaml")
        assert policy.version == 1
        assert len(policy.classifiers) == 1

    def test_minimal_fixture_applies_default_match_mode(self):
        """The minimal fixture omits match_mode; default must be applied."""
        policy = load_classification_policy(FIXTURES / "valid_minimal.yaml")
        rule = policy.classifiers[0]
        assert isinstance(rule, DictionaryRule)
        assert rule.match_mode == DictionaryMatchMode.EXACT_NORMALIZED

    def test_discriminator_dispatches_correctly_for_each_type(self):
        """The valid fixture has one of each rule type; dispatch must produce
        the right concrete class for each."""
        policy = load_classification_policy(FIXTURES / "valid_classification.yaml")
        rules_by_name = {r.name: r for r in policy.classifiers}
        assert isinstance(rules_by_name["pii_columns"], DictionaryRule)
        assert isinstance(rules_by_name["email_pattern"], RegexRule)
        assert isinstance(rules_by_name["ssn_format"], RegexRule)
        assert isinstance(rules_by_name["email_sampling"], StatisticalRule)

    def test_loaded_classification_is_enum_member(self):
        """Classification values must be parsed as ClassificationLabel
        enum members, not plain strings."""
        policy = load_classification_policy(FIXTURES / "valid_minimal.yaml")
        assert policy.classifiers[0].classification == ClassificationLabel.PII

    def test_loads_example_policy_file(self):
        """The shipped example policy must validate against the schema.
        If this test fails, either the example file or the schema is wrong."""
        example_path = (
            Path(__file__).parent.parent.parent
            / "config"
            / "policies"
            / "classification.example.yaml"
        )
        policy = load_classification_policy(example_path)
        assert policy.version >= 1
        assert len(policy.classifiers) > 0

    # ---- Failure modes: PolicyValidationError ----------------------

    def test_unknown_field_raises_validation_error(self):
        with pytest.raises(PolicyValidationError):
            load_classification_policy(FIXTURES / "unknown_field.yaml")

    def test_missing_required_raises_validation_error(self):
        with pytest.raises(PolicyValidationError):
            load_classification_policy(FIXTURES / "missing_required.yaml")

    def test_invalid_classification_label_raises_validation_error(self):
        with pytest.raises(PolicyValidationError):
            load_classification_policy(FIXTURES / "invalid_classification_label.yaml")

    def test_invalid_discriminator_raises_validation_error(self):
        """A rule with an unknown 'type' value fails discriminator dispatch."""
        with pytest.raises(PolicyValidationError):
            load_classification_policy(FIXTURES / "invalid_discriminator.yaml")

    # ---- Failure modes: PolicyLoadError -----------------------------

    def test_malformed_yaml_raises_load_error(self):
        """YAML that can't even be parsed raises PolicyLoadError, not
        PolicyValidationError."""
        with pytest.raises(PolicyLoadError):
            load_classification_policy(FIXTURES / "malformed_yaml.yaml")

    def test_empty_file_raises_load_error(self):
        with pytest.raises(PolicyLoadError):
            load_classification_policy(FIXTURES / "empty.yaml")

    def test_missing_file_raises_load_error(self, tmp_path):
        """A path that doesn't exist raises PolicyLoadError."""
        non_existent = tmp_path / "does_not_exist.yaml"
        with pytest.raises(PolicyLoadError):
            load_classification_policy(non_existent)

    # ---- Error hierarchy --------------------------------------------

    def test_validation_error_is_policy_error(self):
        """PolicyValidationError must be catchable as PolicyError."""
        from dataprism.policy.errors import PolicyError

        with pytest.raises(PolicyError):
            load_classification_policy(FIXTURES / "unknown_field.yaml")

    def test_load_error_is_policy_error(self):
        """PolicyLoadError must be catchable as PolicyError."""
        from dataprism.policy.errors import PolicyError

        with pytest.raises(PolicyError):
            load_classification_policy(FIXTURES / "empty.yaml")

    def test_load_error_preserves_cause(self, tmp_path):
        """When the OS raises FileNotFoundError, the PolicyLoadError's
        __cause__ should point at the original."""
        non_existent = tmp_path / "missing.yaml"
        with pytest.raises(PolicyLoadError) as exc_info:
            load_classification_policy(non_existent)
        # The original OSError should be preserved as __cause__.
        assert isinstance(exc_info.value.__cause__, OSError)

    def test_validation_error_preserves_cause(self):
        """A PolicyValidationError must wrap the original ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(PolicyValidationError) as exc_info:
            load_classification_policy(FIXTURES / "unknown_field.yaml")
        assert isinstance(exc_info.value.__cause__, ValidationError)


class TestLoadAndAuditClassificationPolicy:
    """Tests for the audit-wrapping loader.

    Uses InMemoryStorage so we can inspect exactly which audit events
    were recorded after each call.
    """

    def _service(self):
        """Return a fresh audit service backed by in-memory storage.
        Returns the service AND its storage so tests can read both."""
        storage = InMemoryStorage()
        service = AuditService(storage)
        return service, storage

    # ---- Successful loads record POLICY_LOADED ----------------------

    def test_successful_load_records_policy_loaded_event(self):
        service, storage = self._service()
        load_and_audit_classification_policy(FIXTURES / "valid_minimal.yaml", service)
        events = list(storage.read_all())
        assert len(events) == 1
        assert events[0].event_type == EventType.POLICY_LOADED

    def test_successful_load_records_path_and_metadata(self):
        """The audit event for a successful load includes the path,
        the policy version, and the rules count."""
        service, storage = self._service()
        load_and_audit_classification_policy(FIXTURES / "valid_classification.yaml", service)
        events = list(storage.read_all())
        data = events[0].data
        assert "path" in data
        assert "valid_classification.yaml" in data["path"]
        assert data["version"] == 1
        assert data["rules_count"] == 4

    def test_default_actor_is_policy_loader(self):
        service, storage = self._service()
        load_and_audit_classification_policy(FIXTURES / "valid_minimal.yaml", service)
        events = list(storage.read_all())
        assert events[0].actor == "policy_loader"

    def test_actor_can_be_overridden(self):
        service, storage = self._service()
        load_and_audit_classification_policy(
            FIXTURES / "valid_minimal.yaml", service, actor="cli-user"
        )
        events = list(storage.read_all())
        assert events[0].actor == "cli-user"

    def test_successful_load_returns_the_policy(self):
        """The wrapper must return the same policy the pure loader would."""
        service, _ = self._service()
        policy = load_and_audit_classification_policy(FIXTURES / "valid_minimal.yaml", service)
        assert policy.version == 1
        assert len(policy.classifiers) == 1

    # ---- Failed loads record POLICY_VALIDATION_FAILED ---------------

    def test_validation_failure_records_event(self):
        service, storage = self._service()
        with pytest.raises(PolicyValidationError):
            load_and_audit_classification_policy(FIXTURES / "unknown_field.yaml", service)
        events = list(storage.read_all())
        assert len(events) == 1
        assert events[0].event_type == EventType.POLICY_VALIDATION_FAILED

    def test_load_failure_records_event(self):
        service, storage = self._service()
        with pytest.raises(PolicyLoadError):
            load_and_audit_classification_policy(FIXTURES / "empty.yaml", service)
        events = list(storage.read_all())
        assert len(events) == 1
        assert events[0].event_type == EventType.POLICY_VALIDATION_FAILED

    def test_failure_event_includes_error_metadata(self):
        """Failure events must include the path, the error type, and the
        error message - giving compliance reviewers enough to investigate."""
        service, storage = self._service()
        with pytest.raises(PolicyValidationError):
            load_and_audit_classification_policy(FIXTURES / "unknown_field.yaml", service)
        events = list(storage.read_all())
        data = events[0].data
        assert "path" in data
        assert "unknown_field.yaml" in data["path"]
        assert data["error_type"] == "PolicyValidationError"
        assert "error" in data
        assert isinstance(data["error"], str)

    def test_failure_re_raises_original_exception(self):
        """The audit wrapper records the failure event and then re-raises
        the original exception - it does not swallow errors."""
        service, _ = self._service()
        with pytest.raises(PolicyValidationError):
            load_and_audit_classification_policy(FIXTURES / "unknown_field.yaml", service)

    # ---- Audit chain integrity --------------------------------------

    def test_multiple_loads_accumulate(self):
        """Multiple load calls each produce one audit event in order."""
        service, storage = self._service()
        load_and_audit_classification_policy(FIXTURES / "valid_minimal.yaml", service)
        with pytest.raises(PolicyValidationError):
            load_and_audit_classification_policy(FIXTURES / "unknown_field.yaml", service)
        load_and_audit_classification_policy(FIXTURES / "valid_classification.yaml", service)
        events = list(storage.read_all())
        assert len(events) == 3
        assert [e.event_type for e in events] == [
            EventType.POLICY_LOADED,
            EventType.POLICY_VALIDATION_FAILED,
            EventType.POLICY_LOADED,
        ]
