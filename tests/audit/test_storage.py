"""Tests for AuditStorage implementations.

Covers:
- InMemoryStorage: basic append + read behavior
- JsonLinesStorage: file-backed persistence
- Hash chain integrity: tamper detection across the chain
"""

import json

import pytest

from dataprism.audit.events import AuditEvent, EventType
from dataprism.audit.storage import (
    GENESIS_HASH,
    ChainVerificationError,
    InMemoryStorage,
    JsonLinesStorage,
)


# Shared helper used across multiple test classes.
def _make_event(actor: str = "test") -> AuditEvent:
    """Construct a basic AuditEvent for tests that don't care about details."""
    return AuditEvent(event_type=EventType.POLICY_LOADED, actor=actor)


class TestInMemoryStorage:
    """Basic contract of the in-memory backend."""

    def test_fresh_storage_is_empty(self):
        storage = InMemoryStorage()
        assert list(storage.read_all()) == []

    def test_single_append_persists(self):
        storage = InMemoryStorage()
        event = _make_event()
        storage.append(event)
        stored = list(storage.read_all())
        assert len(stored) == 1
        assert stored[0] == event

    def test_multiple_appends_preserve_order(self):
        storage = InMemoryStorage()
        events = [_make_event(actor=f"actor_{i}") for i in range(5)]
        for e in events:
            storage.append(e)
        stored = list(storage.read_all())
        assert stored == events

    def test_verify_is_a_noop(self):
        """In-memory storage has no integrity layer; verify() must not raise."""
        storage = InMemoryStorage()
        storage.append(_make_event())
        storage.verify()  # should not raise

    def test_instances_are_independent(self):
        """Each instance has its own state - no shared list."""
        s1 = InMemoryStorage()
        s2 = InMemoryStorage()
        s1.append(_make_event(actor="alice"))
        assert list(s2.read_all()) == []


class TestJsonLinesStorageBasics:
    """Append-and-read behavior of the file-backed storage."""

    def test_fresh_file_is_empty(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        storage = JsonLinesStorage(path)
        assert list(storage.read_all()) == []

    def test_single_append_creates_file(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        storage = JsonLinesStorage(path)
        storage.append(_make_event())
        assert path.exists()

    def test_appended_event_round_trips(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        storage = JsonLinesStorage(path)
        event = _make_event(actor="alice")
        storage.append(event)

        stored = list(storage.read_all())
        assert len(stored) == 1
        # event_id, timestamp, etc. all preserved through serialization
        assert stored[0].event_id == event.event_id
        assert stored[0].actor == event.actor
        assert stored[0].event_type == event.event_type

    def test_multiple_appends_persist_in_order(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        storage = JsonLinesStorage(path)
        events = [_make_event(actor=f"actor_{i}") for i in range(3)]
        for e in events:
            storage.append(e)

        stored = list(storage.read_all())
        assert [e.actor for e in stored] == ["actor_0", "actor_1", "actor_2"]

    def test_storage_resumes_from_existing_file(self, tmp_path):
        """A new storage instance pointed at an existing file should
        correctly chain to the last record."""
        path = tmp_path / "audit.jsonl"
        s1 = JsonLinesStorage(path)
        s1.append(_make_event(actor="first"))

        # Drop s1, create s2 against the same file
        s2 = JsonLinesStorage(path)
        s2.append(_make_event(actor="second"))

        stored = list(s2.read_all())
        assert [e.actor for e in stored] == ["first", "second"]


class TestHashChain:
    """The integrity-critical tests - the hash chain links must hold."""

    def test_first_record_links_to_genesis(self, tmp_path):
        """A fresh chain's first record must have prev_hash == GENESIS_HASH."""
        path = tmp_path / "audit.jsonl"
        storage = JsonLinesStorage(path)
        storage.append(_make_event())

        # Read the raw file content to inspect chain bookkeeping
        line = path.read_text().strip()
        record = json.loads(line)
        assert record["prev_hash"] == GENESIS_HASH

    def test_subsequent_records_chain_to_previous(self, tmp_path):
        """Each record's prev_hash must equal the previous record's hash."""
        path = tmp_path / "audit.jsonl"
        storage = JsonLinesStorage(path)
        storage.append(_make_event(actor="first"))
        storage.append(_make_event(actor="second"))

        lines = path.read_text().strip().split("\n")
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert second["prev_hash"] == first["hash"]

    def test_verify_passes_on_intact_chain(self, tmp_path):
        """Untampered logs verify cleanly."""
        path = tmp_path / "audit.jsonl"
        storage = JsonLinesStorage(path)
        for i in range(10):
            storage.append(_make_event(actor=f"actor_{i}"))
        storage.verify()  # should not raise

    def test_verify_detects_tampered_record(self, tmp_path):
        """If a past record is modified, verify() raises pointing at the
        position where the chain first breaks."""
        path = tmp_path / "audit.jsonl"
        storage = JsonLinesStorage(path)
        for i in range(5):
            storage.append(_make_event(actor=f"actor_{i}"))

        # Tamper with the third record (index 2): change actor.
        lines = path.read_text().strip().split("\n")
        record = json.loads(lines[2])
        record["actor"] = "MALICIOUS"
        lines[2] = json.dumps(record)
        path.write_text("\n".join(lines) + "\n")

        with pytest.raises(ChainVerificationError) as exc_info:
            storage.verify()
        # The break is detected at the tampered record itself (its content
        # no longer matches its stored hash).
        assert exc_info.value.position == 2

    def test_verify_detects_broken_chain_link(self, tmp_path):
        """If a record's prev_hash is changed to break the chain, verify
        detects it at the affected position."""
        path = tmp_path / "audit.jsonl"
        storage = JsonLinesStorage(path)
        for i in range(5):
            storage.append(_make_event(actor=f"actor_{i}"))

        # Corrupt the prev_hash on record index 3 to break the chain link.
        lines = path.read_text().strip().split("\n")
        record = json.loads(lines[3])
        record["prev_hash"] = "f" * 64  # arbitrary wrong hash
        lines[3] = json.dumps(record)
        path.write_text("\n".join(lines) + "\n")

        with pytest.raises(ChainVerificationError) as exc_info:
            storage.verify()
        assert exc_info.value.position == 3

    def test_verify_detects_inserted_record(self, tmp_path):
        """If someone inserts a fake record between two real ones, the
        chain breaks at the insertion point."""
        path = tmp_path / "audit.jsonl"
        storage = JsonLinesStorage(path)
        for i in range(3):
            storage.append(_make_event(actor=f"actor_{i}"))

        # Read existing lines, then insert a fake record between index 0 and 1.
        lines = path.read_text().strip().split("\n")
        fake = {
            "event_id": "00000000-0000-0000-0000-000000000000",
            "event_type": "policy_loaded",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "actor": "ATTACKER",
            "data": {},
            "prev_hash": "f" * 64,
            "hash": "e" * 64,
        }
        lines.insert(1, json.dumps(fake))
        path.write_text("\n".join(lines) + "\n")

        with pytest.raises(ChainVerificationError):
            storage.verify()

    def test_chain_verification_error_carries_position(self, tmp_path):
        """The error must expose the position attribute, not just a message."""
        path = tmp_path / "audit.jsonl"
        storage = JsonLinesStorage(path)
        for i in range(3):
            storage.append(_make_event(actor=f"actor_{i}"))

        # Tamper with record 1
        lines = path.read_text().strip().split("\n")
        record = json.loads(lines[1])
        record["actor"] = "x"
        lines[1] = json.dumps(record)
        path.write_text("\n".join(lines) + "\n")

        with pytest.raises(ChainVerificationError) as exc_info:
            storage.verify()
        assert hasattr(exc_info.value, "position")
        assert isinstance(exc_info.value.position, int)
