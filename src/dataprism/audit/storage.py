"""Storage backends for the audit log.

This module defines the AuditStorage protocol and two implementations:

- InMemoryStorage: backed by a list, used in tests and for demos.
  Does not persist anything; instances start empty.
- JsonLinesStorage: backed by a .jsonl file on disk. Each event is
  serialized as one JSON object per line. Includes a SHA-256 hash chain
  for tamper detection.

The hash chain works as follows: every persisted record includes the
hash of the previous record. The first record references a fixed
"genesis" hash. Tampering with any past record breaks the chain at
that point and everywhere downstream. Verification walks the chain
and confirms each link.

Single-writer assumption: JsonLinesStorage does not synchronize
concurrent writes. Two processes appending simultaneously will produce
a corrupt chain. For multi-writer scenarios use a future database
backend or external file locking.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from dataprism.audit.events import AuditEvent
from dataprism.core.exceptions import DataprismError

GENESIS_HASH = "0" * 64
"""Placeholder hash for the start of a fresh chain.

64 zero characters because SHA-256 hex digests are 64 characters long.
This value is what the first event's prev_hash points to.
"""


class AuditStorageError(DataprismError):
    """Raised for audit storage failures (corruption, IO, chain break)."""


class ChainVerificationError(AuditStorageError):
    """Raised when audit log integrity verification detects tampering.

    Includes the position (zero-indexed) in the log where the chain
    first failed to verify, so callers can pinpoint the damage.
    """

    def __init__(self, message: str, position: int) -> None:
        super().__init__(message)
        self.position = position


class AuditStorage(Protocol):
    """Contract for any audit event storage backend.

    Implementations must be append-only: append() persists a new event,
    read_all() yields events in insertion order. Implementations decide
    their own durability and concurrency guarantees.
    """

    def append(self, event: AuditEvent) -> None:
        """Persist a single event. Implementations must not modify the event."""
        ...

    def read_all(self) -> Iterator[AuditEvent]:
        """Yield all stored events in insertion order."""
        ...

    def verify(self) -> None:
        """Check the integrity of the stored log.

        Raises ChainVerificationError if tampering is detected.
        Implementations without integrity checking can make this a no-op.
        """
        ...


class InMemoryStorage:
    """List-backed storage for testing and demos.

    Events are kept in memory only. Nothing persists across instances
    or process restarts. verify() always succeeds since there is no
    persistence layer to tamper with.
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    def read_all(self) -> Iterator[AuditEvent]:
        yield from self._events

    def verify(self) -> None:
        # Nothing to verify - in-memory storage has no persistence layer.
        return


def _hash_record(record: dict) -> str:
    """Compute the SHA-256 hash of a record's content.

    The record dict must NOT include the 'hash' field itself (we'd be
    hashing a value that depends on what we're computing - chicken
    and egg). Records are serialized with sorted keys and no
    whitespace to guarantee deterministic hashing across machines
    and Python versions.
    """
    serialized = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class JsonLinesStorage:
    """Production storage backend: append to a .jsonl file with hash chaining.

    Each line of the file is a JSON object representing one event plus
    chain bookkeeping. The bookkeeping fields are:

        prev_hash: SHA-256 hex of the previous record's content (with
            its own prev_hash included). The first record's prev_hash
            is GENESIS_HASH.
        hash: SHA-256 hex of this record's content (including prev_hash
            but excluding the hash field itself).

    On instantiation, the last record's hash is read so subsequent
    appends can correctly chain to it.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._last_hash = self._read_last_hash()

    def _read_last_hash(self) -> str:
        """Find the hash of the last record in the file.

        Returns GENESIS_HASH for empty or missing files (fresh chain).
        Reads the file from the beginning to find the last line; for
        v1 this is fine. A future optimization could seek to the end
        and read backwards.
        """
        if not self.path.exists():
            return GENESIS_HASH
        last_hash = GENESIS_HASH
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                last_hash = record["hash"]
        return last_hash

    def append(self, event: AuditEvent) -> None:
        record = event.model_dump(mode="json")
        record["prev_hash"] = self._last_hash
        record["hash"] = _hash_record(record)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        self._last_hash = record["hash"]

    def read_all(self) -> Iterator[AuditEvent]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                # Strip the chain bookkeeping fields before reconstructing
                # the AuditEvent - those fields live in storage, not on
                # the in-memory event model.
                record.pop("prev_hash", None)
                record.pop("hash", None)
                yield AuditEvent.model_validate(record)

    def verify(self) -> None:
        """Walk the chain and confirm each link.

        Raises ChainVerificationError on the first detected break,
        including the zero-indexed position of the offending record.
        """
        if not self.path.exists():
            return  # Empty chain - vacuously valid
        expected_prev = GENESIS_HASH
        with self.path.open("r", encoding="utf-8") as f:
            for position, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                stored_hash = record.pop("hash")
                if record.get("prev_hash") != expected_prev:
                    raise ChainVerificationError(
                        f"Chain broken at position {position}: prev_hash mismatch",
                        position=position,
                    )
                recomputed = _hash_record(record)
                if recomputed != stored_hash:
                    raise ChainVerificationError(
                        f"Chain broken at position {position}: "
                        f"record content does not match stored hash",
                        position=position,
                    )
                expected_prev = stored_hash
