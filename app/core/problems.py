"""RFC 9457 problem details, the one error response format of every route."""

from collections.abc import Mapping

from starlette.responses import JSONResponse

from app.core.json_types import JsonValue


class ProblemResponse(JSONResponse):
    """`problem_type` is a snake_case slug; None means a bare HTTP error."""

    media_type = "application/problem+json"

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        problem_type: str | None = None,
        headers: Mapping[str, str] | None = None,
        extensions: Mapping[str, JsonValue] | None = None,
    ) -> None:
        content: dict[str, JsonValue] = {
            "type": f"/problems/{problem_type}" if problem_type else "about:blank",
            "status": status_code,
            "detail": detail,
        }
        # An extension adds a member but never replaces a standard one.
        for name, value in (extensions or {}).items():
            content.setdefault(name, value)
        super().__init__(content, status_code=status_code, headers=headers)
