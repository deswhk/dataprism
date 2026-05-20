"""Audit service: the public API for recording governance events.

Callers use AuditService.record() to log that something happened.
The service constructs an AuditEvent and hands it to the storage
backend. This indirection keeps event construction in one place
(so future concerns like redaction, sampling, or trace correlation
can be added without touching every call site) and decouples
callers from the storage implementation.
"""

from __future__ import annotations

from typing import Any

from dataprism.audit.events import AuditEvent, EventType
from dataprism.audit.storage import AuditStorage


class AuditService:
    """Records governance events to a storage backend.

    The service is intentionally thin. It exists to centralize event
    construction and to keep callers decoupled from storage details.
    Future cross-cutting concerns (rate limiting, redaction, trace
    correlation) belong here, not in storage.

    Example:
        from pathlib import Path
        from dataprism.audit.events import EventType
        from dataprism.audit.storage import JsonLinesStorage
        from dataprism.audit.service import AuditService

        storage = JsonLinesStorage(Path("audit.jsonl"))
        service = AuditService(storage)
        service.record(
            event_type=EventType.POLICY_LOADED,
            actor="cli",
            data={"policy_file": "classification.yaml"},
        )
    """

    def __init__(self, storage: AuditStorage) -> None:
        """Construct a service backed by the given storage.

        Args:
            storage: Any object satisfying the AuditStorage protocol.
                For tests, use InMemoryStorage. For production, use
                JsonLinesStorage or a future database-backed storage.
        """
        self._storage = storage

    def record(
        self,
        event_type: EventType,
        actor: str,
        data: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Record a single audit event.

        Constructs an AuditEvent from the provided fields and persists
        it via the configured storage. The constructed event is also
        returned, primarily for testing and debugging - callers usually
        do not need to use the return value.

        Args:
            event_type: The kind of event being recorded.
            actor: Who or what triggered the event (e.g. username,
                service name, "cli", "scheduler").
            data: Event-type-specific payload. Defaults to empty dict
                if not provided.

        Returns:
            The constructed AuditEvent (which has already been
            persisted to storage).
        """
        event = AuditEvent(
            event_type=event_type,
            actor=actor,
            data=data or {},
        )
        self._storage.append(event)
        return event
