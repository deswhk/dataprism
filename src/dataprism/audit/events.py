"""Audit event model and event type enumeration.

Defines what an audit event looks like before it's persisted. Storage
backends add bookkeeping fields (prev_hash, hash) when writing to disk;
those fields live in the storage layer, not on the AuditEvent itself.
"""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    """The kind of audit event being recorded.

    Using StrEnum lets these values serialize to JSON as plain strings
    (e.g. "policy_loaded" rather than "EventType.POLICY_LOADED"), which
    keeps the audit log human-readable.

    New event types should be added here as the system grows. Removing
    or renaming an existing value is a breaking change to historical
    audit logs and should be done with care.
    """

    POLICY_LOADED = "policy_loaded"
    POLICY_VALIDATION_FAILED = "policy_validation_failed"
    CLASSIFICATION_RUN = "classification_run"
    CLASSIFICATION_FAILED = "classification_failed"
    QUALITY_CHECK_RUN = "quality_check_run"
    QUALITY_CHECK_FAILED = "quality_check_failed"


def _utc_now() -> datetime:
    """Timezone-aware current time in UTC.

    Pulled out as a function so tests can monkey-patch a fixed time.
    """
    return datetime.now(timezone.utc)


class AuditEvent(BaseModel):
    """An immutable record of something that happened.

    Audit events are created by dataprism subsystems and persisted by
    AuditStorage implementations. Once an event has been recorded it
    must not be modified - tamper-evidence depends on this.

    Attributes:
        event_id: Unique identifier assigned at creation. Useful for
            cross-referencing from other systems.
        event_type: The kind of event (see EventType enum).
        timestamp: When the event occurred, always UTC.
        actor: Who or what triggered the event. Free-form string;
            could be a username, service name, "cli", "scheduler", etc.
        data: Event-type-specific payload. Different event types have
            different expected fields; the engine layer (not this model)
            is responsible for putting the right data in the right type.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    event_id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    timestamp: datetime = Field(default_factory=_utc_now)
    actor: str
    data: dict[str, Any] = Field(default_factory=dict)
