"""Audit logging subsystem.

Append-only event log with tamper-evident hash chaining. Other dataprism
subsystems write audit events here; compliance reviews read them back.

Public API:
    AuditEvent     - the immutable event record
    EventType      - enumeration of event kinds
    AuditService   - the write-side API
    AuditStorage   - the storage protocol
"""
