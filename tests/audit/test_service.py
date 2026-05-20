"""Tests for AuditService.

The service is a thin wrapper that constructs AuditEvents and dispatches
to a storage backend. Tests verify dispatch behavior and event construction.
"""

from dataprism.audit.events import AuditEvent, EventType
from dataprism.audit.service import AuditService
from dataprism.audit.storage import InMemoryStorage


class TestAuditServiceBasics:
    """The service records events and dispatches to storage."""

    def test_record_persists_event_to_storage(self):
        """A recorded event must appear in the underlying storage."""
        storage = InMemoryStorage()
        service = AuditService(storage)
        service.record(EventType.POLICY_LOADED, actor="cli")

        stored = list(storage.read_all())
        assert len(stored) == 1

    def test_record_returns_constructed_event(self):
        """record() returns the AuditEvent it constructed - useful for tests."""
        storage = InMemoryStorage()
        service = AuditService(storage)
        result = service.record(EventType.POLICY_LOADED, actor="cli")
        assert isinstance(result, AuditEvent)

    def test_returned_event_matches_stored_event(self):
        """The returned event and the stored event are the same instance."""
        storage = InMemoryStorage()
        service = AuditService(storage)
        returned = service.record(EventType.POLICY_LOADED, actor="cli")

        stored = list(storage.read_all())
        assert stored[0] is returned

    def test_record_preserves_event_type(self):
        storage = InMemoryStorage()
        service = AuditService(storage)
        service.record(EventType.CLASSIFICATION_RUN, actor="cli")

        stored = list(storage.read_all())
        assert stored[0].event_type == EventType.CLASSIFICATION_RUN

    def test_record_preserves_actor(self):
        storage = InMemoryStorage()
        service = AuditService(storage)
        service.record(EventType.POLICY_LOADED, actor="alice@example.com")

        stored = list(storage.read_all())
        assert stored[0].actor == "alice@example.com"


class TestAuditServiceDataHandling:
    """Behavior around the optional data payload."""

    def test_data_defaults_to_empty_dict(self):
        """Calling record() without data should produce an event with data={}."""
        storage = InMemoryStorage()
        service = AuditService(storage)
        service.record(EventType.POLICY_LOADED, actor="cli")

        stored = list(storage.read_all())
        assert stored[0].data == {}

    def test_data_passes_through_unchanged(self):
        """A data dict provided to record() is preserved on the event."""
        storage = InMemoryStorage()
        service = AuditService(storage)
        payload = {"file": "policy.yaml", "rules_loaded": 14}
        service.record(EventType.POLICY_LOADED, actor="cli", data=payload)

        stored = list(storage.read_all())
        assert stored[0].data == payload

    def test_passing_none_data_yields_empty_dict(self):
        """Explicitly passing data=None must not raise; treated as empty."""
        storage = InMemoryStorage()
        service = AuditService(storage)
        service.record(EventType.POLICY_LOADED, actor="cli", data=None)

        stored = list(storage.read_all())
        assert stored[0].data == {}


class TestAuditServiceDispatch:
    """The service correctly dispatches to multiple storage instances."""

    def test_multiple_records_accumulate(self):
        storage = InMemoryStorage()
        service = AuditService(storage)
        service.record(EventType.POLICY_LOADED, actor="alice")
        service.record(EventType.CLASSIFICATION_RUN, actor="alice")
        service.record(EventType.QUALITY_CHECK_RUN, actor="bob")

        stored = list(storage.read_all())
        assert len(stored) == 3
        assert [e.actor for e in stored] == ["alice", "alice", "bob"]

    def test_two_services_with_separate_storage_dont_interfere(self):
        """Each service uses its own storage; they're fully independent."""
        storage_a = InMemoryStorage()
        storage_b = InMemoryStorage()
        service_a = AuditService(storage_a)
        service_b = AuditService(storage_b)

        service_a.record(EventType.POLICY_LOADED, actor="a")
        service_b.record(EventType.POLICY_LOADED, actor="b")

        assert len(list(storage_a.read_all())) == 1
        assert len(list(storage_b.read_all())) == 1
        assert list(storage_a.read_all())[0].actor == "a"
        assert list(storage_b.read_all())[0].actor == "b"
