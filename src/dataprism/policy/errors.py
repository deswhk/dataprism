"""Exception hierarchy for the policy subsystem.

Three levels:
    PolicyError              - base class; catch this to handle any policy issue
    PolicyLoadError          - file could not be read or YAML could not be parsed
    PolicyValidationError    - YAML parsed but does not match the schema

This separation lets callers handle different failure modes differently.
A transient file issue might be retried; a schema mismatch should not.
"""

from dataprism.core.exceptions import DataprismError


class PolicyError(DataprismError):
    """Base class for all policy subsystem errors.

    Catch this to handle any error originating from policy loading or
    validation. For more granular handling, catch PolicyLoadError or
    PolicyValidationError specifically.
    """


class PolicyLoadError(PolicyError):
    """Raised when a policy file cannot be read or parsed as YAML.

    Causes include:
        - The file does not exist
        - The file exists but cannot be read (permissions)
        - The file contents are not valid YAML

    The original exception is preserved as the __cause__ for debugging.
    """


class PolicyValidationError(PolicyError):
    """Raised when a policy file parses but does not match the schema.

    Causes include:
        - Missing required fields
        - Unknown fields (Pydantic's extra='forbid')
        - Wrong types
        - Invalid enum values (e.g. classification label not in the allowed set)
        - Failed discriminator dispatch (rule type doesn't match expected types)

    The original Pydantic ValidationError is preserved as __cause__,
    giving callers full access to the structured error details.
    """
