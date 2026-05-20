"""Base exception hierarchy for dataprism.

All custom exceptions raised by dataprism inherit from DataprismError.
Subpackages define their own specific exceptions (e.g. AuditError,
PolicyError) that inherit from DataprismError, allowing callers to
catch all dataprism-originated errors with a single except clause.
"""


class DataprismError(Exception):
    """Base class for all exceptions raised by dataprism.

    Catching this exception will catch any error originating from
    dataprism code (excluding Python built-in errors and errors from
    third-party libraries used internally).

    Subpackages should define their own exception classes inheriting
    from this base. For example:

        class AuditError(DataprismError):
            '''Raised for audit subsystem errors.'''

        class PolicyError(DataprismError):
            '''Raised for policy loading or validation errors.'''
    """
