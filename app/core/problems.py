"""RFC 9457 problem details, the one error response format of every route."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.core.json_types import JsonValue

PROBLEM_MEDIA_TYPE: Final[str] = "application/problem+json"


def problem_response(
    *,
    status_code: int,
    detail: str,
    problem_type: str | None = None,
    headers: Mapping[str, str] | None = None,
    extensions: Mapping[str, JsonValue] | None = None,
) -> JSONResponse:
    """Render one problem as an `application/problem+json` response.

    `problem_type` is an application problem slug in snake_case, rendered as the relative
    URI `/problems/<slug>` with a title derived from the slug. `None` renders `about:blank`
    with the HTTP reason phrase as the title, for errors that carry no meaning beyond their
    status code. Extension members never replace the standard members.
    """
    if problem_type is None:
        type_uri = "about:blank"
        title = _reason_phrase(status_code)
    else:
        type_uri = f"/problems/{problem_type}"
        title = problem_type.replace("_", " ").capitalize()
    content: dict[str, JsonValue] = {
        **(extensions or {}),
        "type": type_uri,
        "title": title,
        "status": status_code,
        "detail": detail,
    }
    return JSONResponse(
        content,
        status_code=status_code,
        headers=headers,
        media_type=PROBLEM_MEDIA_TYPE,
    )


def _reason_phrase(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"
