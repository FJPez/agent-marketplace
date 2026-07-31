"""Base application exception taxonomy.

Services raise these exceptions (or subclasses of them) and the API layer
translates them to HTTP responses via `app.api.exception_handlers`.

Note that `InvalidStateError` shadows `asyncio.InvalidStateError` by name only.
Import it qualified wherever both are used in the same module.
"""


class NotFoundError(Exception):
    """Requested resource does not exist; translates to HTTP 404."""


class UnauthenticatedError(Exception):
    """Request lacks valid authentication credentials; translates to HTTP 401."""


class ConflictError(Exception):
    """Request conflicts with current resource state; translates to HTTP 409."""


class PermissionDeniedError(Exception):
    """Actor is authenticated but not allowed to perform the action; translates to HTTP 403."""


class InvalidInputError(Exception):
    """Input violates an application rule; translates to HTTP 422."""


class InvalidStateError(Exception):
    """Operation is not valid for the entity's current lifecycle state; translates to HTTP 409."""
