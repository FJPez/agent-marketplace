import json

import pytest
from starlette.responses import Response

from app.core.problems import PROBLEM_MEDIA_TYPE, problem_response


def _body(response: Response) -> object:
    return json.loads(bytes(response.body))


def test_application_problem_renders_relative_type_and_derived_title() -> None:
    response = problem_response(
        status_code=409,
        problem_type="in_progress",
        detail="purchase is still in progress",
        headers={"Retry-After": "2"},
        extensions={"invocation_id": 7},
    )

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.headers["retry-after"] == "2"
    assert _body(response) == {
        "type": "/problems/in_progress",
        "title": "In progress",
        "status": 409,
        "detail": "purchase is still in progress",
        "invocation_id": 7,
    }


@pytest.mark.parametrize(
    ("status_code", "expected_title"),
    [(404, "Not Found"), (499, "Error")],
    ids=["reason_phrase", "unregistered_status"],
)
def test_problem_without_type_renders_about_blank(status_code: int, expected_title: str) -> None:
    response = problem_response(status_code=status_code, detail="something went wrong")

    assert response.status_code == status_code
    assert response.headers["content-type"] == "application/problem+json"
    assert _body(response) == {
        "type": "about:blank",
        "title": expected_title,
        "status": status_code,
        "detail": "something went wrong",
    }


def test_extensions_never_replace_standard_members() -> None:
    response = problem_response(
        status_code=409,
        problem_type="conflict",
        detail="real detail",
        extensions={"type": "spoofed", "title": "spoofed", "status": 200, "detail": "spoofed"},
    )

    body = _body(response)
    assert body == {
        "type": "/problems/conflict",
        "title": "Conflict",
        "status": 409,
        "detail": "real detail",
    }
    assert isinstance(body, dict)
    assert list(body.keys())[:4] == ["type", "title", "status", "detail"]
