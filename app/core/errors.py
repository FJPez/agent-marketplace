"""Base application exception taxonomy.

Services raise these exceptions (or subclasses of them) and the API layer
translates them to `application/problem+json` responses via
`app.api.exception_handlers`, which maps each class to a status code and a
default problem type.

Note that `InvalidStateError` shadows `asyncio.InvalidStateError` by name only.
Import it qualified wherever both are used in the same module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.core.json_types import JsonValue


class ApplicationError(Exception):
    """Base of the taxonomy; the message becomes the problem's `detail`.

    `problem_type` replaces the class's default problem type with a more specific
    snake_case slug (for example `in_progress` on a `ConflictError`). `headers` (for
    example `Retry-After`) are added to the response and `extensions` to the problem
    body.
    """

    def __init__(
        self,
        message: str,
        *,
        problem_type: str | None = None,
        headers: Mapping[str, str] | None = None,
        extensions: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.problem_type = problem_type
        self.headers: dict[str, str] = dict(headers or {})
        self.extensions: dict[str, JsonValue] = dict(extensions or {})


class NotFoundError(ApplicationError):
    """Requested resource does not exist; translates to HTTP 404."""


class UnauthenticatedError(ApplicationError):
    """Request lacks valid authentication credentials; translates to HTTP 401."""


class ConflictError(ApplicationError):
    """Request conflicts with current resource state; translates to HTTP 409."""


class PermissionDeniedError(ApplicationError):
    """Actor is authenticated but not allowed to perform the action; translates to HTTP 403."""


class InvalidInputError(ApplicationError):
    """Input violates an application rule; translates to HTTP 422."""


class InvalidStateError(ApplicationError):
    """Operation is not valid for the entity's current lifecycle state; translates to HTTP 409."""


class UnavailableError(ApplicationError):
    """The server cannot handle the request now; translates to HTTP 503."""


class UpstreamError(ApplicationError):
    """The upstream provider call failed; translates to HTTP 502."""


class UpstreamTimeoutError(ApplicationError):
    """The upstream provider call timed out; translates to HTTP 504."""
