"""Base application exception taxonomy.

Services raise these exceptions (or subclasses of them) and the API layer renders
them as `application/problem+json` from the status code and problem type each
class declares.

Note that `InvalidStateError` shadows `asyncio.InvalidStateError` by name only.
Import it qualified wherever both are used in the same module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from app.core.json_types import JsonValue


class ApplicationError(Exception):
    """Base of the taxonomy; the message becomes the problem's `detail`.

    `problem_type` replaces the class's default problem type with a more specific
    snake_case slug (for example `in_progress` on a `ConflictError`). `headers` (for
    example `Retry-After`) are added to the response and `extensions` to the problem
    body.
    """

    status_code: ClassVar[int] = 500
    default_problem_type: ClassVar[str] = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        problem_type: str | None = None,
        headers: Mapping[str, str] | None = None,
        extensions: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.problem_type = problem_type or self.default_problem_type
        self.headers: dict[str, str] = dict(headers or {})
        self.extensions: dict[str, JsonValue] = dict(extensions or {})


class NotFoundError(ApplicationError):
    """Requested resource does not exist."""

    status_code = 404
    default_problem_type = "not_found"


class UnauthenticatedError(ApplicationError):
    """Request lacks valid authentication credentials."""

    status_code = 401
    default_problem_type = "unauthenticated"


class ConflictError(ApplicationError):
    """Request conflicts with current resource state."""

    status_code = 409
    default_problem_type = "conflict"


class PermissionDeniedError(ApplicationError):
    """Actor is authenticated but not allowed to perform the action."""

    status_code = 403
    default_problem_type = "permission_denied"


class InvalidInputError(ApplicationError):
    """Input violates an application rule."""

    status_code = 422
    default_problem_type = "invalid_input"


class InvalidStateError(ApplicationError):
    """Operation is not valid for the entity's current lifecycle state."""

    status_code = 409
    default_problem_type = "invalid_state"


class UnavailableError(ApplicationError):
    """The server cannot handle the request now."""

    status_code = 503
    default_problem_type = "unavailable"


class UpstreamError(ApplicationError):
    """The upstream provider call failed."""

    status_code = 502
    default_problem_type = "upstream_error"


class UpstreamTimeoutError(ApplicationError):
    """The upstream provider call timed out."""

    status_code = 504
    default_problem_type = "upstream_timeout"
