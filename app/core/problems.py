"""RFC 9457 problem details, the one error response format of every route."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Final

from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from app.core.json_types import JsonValue

PROBLEM_MEDIA_TYPE: Final[str] = "application/problem+json"


class Problem(BaseModel):
    """The body of every error response; a problem may add extension members."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(description="`/problems/<slug>`, or `about:blank` for a bare HTTP error.")
    title: str = Field(description="Short summary of the problem type.")
    status: int = Field(description="The HTTP status code of the response.")
    detail: str = Field(description="What went wrong in this request.")


def problem_response(
    *,
    status_code: int,
    detail: str,
    problem_type: str | None = None,
    headers: Mapping[str, str] | None = None,
    extensions: Mapping[str, JsonValue] | None = None,
) -> JSONResponse:
    """Render one problem as an `application/problem+json` response.

    `problem_type` is a snake_case slug. `None` renders `about:blank`, for errors that
    carry no meaning beyond their status code.
    """
    if problem_type is None:
        type_uri = "about:blank"
        title = _reason_phrase(status_code)
    else:
        type_uri = f"/problems/{problem_type}"
        title = problem_type.replace("_", " ").capitalize()
    # The standard members come last so an extension can never replace one.
    problem = Problem.model_validate(
        {
            **(extensions or {}),
            "type": type_uri,
            "title": title,
            "status": status_code,
            "detail": detail,
        }
    )
    return JSONResponse(
        problem.model_dump(),
        status_code=status_code,
        headers=headers,
        media_type=PROBLEM_MEDIA_TYPE,
    )


def _reason_phrase(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"
