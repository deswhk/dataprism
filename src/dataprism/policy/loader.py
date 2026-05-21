"""YAML loaders for policy files.

Two functions are provided:

- load_classification_policy: pure loader, validates YAML against the
  schema, raises PolicyLoadError or PolicyValidationError on failure.
  No side effects.

- load_and_audit_classification_policy: convenience wrapper that
  records audit events on success (POLICY_LOADED) and failure
  (POLICY_VALIDATION_FAILED). The audit event is recorded BEFORE
  any exception is re-raised, so failures are always logged.

The split lets the pure loader be tested without an AuditService and
keeps the cross-cutting audit concern out of the loading logic.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from dataprism.audit.events import EventType
from dataprism.audit.service import AuditService
from dataprism.policy.errors import PolicyLoadError, PolicyValidationError
from dataprism.policy.models import ClassificationPolicy


def load_classification_policy(path: Path) -> ClassificationPolicy:
    """Load and validate a classification policy file.

    Args:
        path: Path to a YAML file containing a classification policy.

    Returns:
        The parsed and validated ClassificationPolicy.

    Raises:
        PolicyLoadError: The file could not be read or parsed as YAML.
            Causes include missing file, permission errors, and
            malformed YAML.
        PolicyValidationError: The file parsed as YAML but does not
            match the policy schema. The original Pydantic
            ValidationError is preserved as __cause__.
    """
    # Stage 1: read and parse YAML. Failures here are PolicyLoadError.
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PolicyLoadError(f"Could not read policy file {path}: {e}") from e

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PolicyLoadError(f"Could not parse {path} as YAML: {e}") from e

    # YAML loading an empty file returns None; treat that as a load error.
    if data is None:
        raise PolicyLoadError(f"Policy file {path} is empty")

    # Stage 2: validate against the schema. Failures here are PolicyValidationError.
    try:
        return ClassificationPolicy.model_validate(data)
    except ValidationError as e:
        raise PolicyValidationError(f"Policy file {path} does not match the schema: {e}") from e


def load_and_audit_classification_policy(
    path: Path,
    audit_service: AuditService,
    actor: str = "policy_loader",
) -> ClassificationPolicy:
    """Load a classification policy and record audit events.

    On success, records POLICY_LOADED. On failure, records
    POLICY_VALIDATION_FAILED and re-raises the original exception.

    Args:
        path: Path to a YAML file containing a classification policy.
        audit_service: Service to record audit events through.
        actor: Who or what triggered the load. Defaults to
            "policy_loader" for programmatic calls; callers should
            override with a meaningful identifier (CLI user, service
            name, etc.).

    Returns:
        The parsed and validated ClassificationPolicy.

    Raises:
        PolicyLoadError, PolicyValidationError: Same as
            load_classification_policy. The audit event is recorded
            before the exception is re-raised.
    """
    try:
        policy = load_classification_policy(path)
    except (PolicyLoadError, PolicyValidationError) as e:
        audit_service.record(
            event_type=EventType.POLICY_VALIDATION_FAILED,
            actor=actor,
            data={
                "path": str(path),
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        raise

    audit_service.record(
        event_type=EventType.POLICY_LOADED,
        actor=actor,
        data={
            "path": str(path),
            "version": policy.version,
            "rules_count": len(policy.classifiers),
        },
    )
    return policy
