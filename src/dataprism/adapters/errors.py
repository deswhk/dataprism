"""Exception hierarchy for the database adapter subsystem.

Two distinct failure categories, each with its own exception type:

- AdapterConnectionError: the connection itself failed. Bad DSN,
  unreachable server, authentication failure. Sometimes recoverable
  (retry might help if transient).
- AdapterQueryError: connection succeeded but a query failed. Missing
  table, missing column, schema mismatch. Generally needs a code or
  configuration fix, not a retry.

Both inherit from AdapterError so callers can catch all adapter
failures with one except clause. AdapterError in turn inherits from
DataprismError, the project-wide base.
"""

from __future__ import annotations

from dataprism.core.exceptions import DataprismError


class AdapterError(DataprismError):
    """Base class for all database adapter exceptions."""


class AdapterConnectionError(AdapterError):
    """Raised when an adapter cannot establish a connection.

    Examples: malformed DSN, network unreachable, authentication
    failed, insufficient permissions to connect, database does not
    exist.

    Sometimes recoverable - a retry might succeed if the underlying
    cause was transient (e.g., temporary network issue). Callers
    deciding whether to retry should consider the original cause,
    accessible via the exception's __cause__ attribute when raised
    with `raise ... from e`.
    """


class AdapterQueryError(AdapterError):
    """Raised when an adapter operation fails after connecting.

    Examples: requested table does not exist, requested column does
    not exist, malformed query, schema introspection failure.

    Generally not recoverable by retry - usually indicates a code bug
    or a configuration mismatch. Caller should fix the request rather
    than retry it.
    """
