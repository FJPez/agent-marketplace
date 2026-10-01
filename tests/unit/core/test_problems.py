import json

from starlette.responses import Response

from app.core.problems import ProblemResponse


def _body(response: Response) -> object:
    return json.loads(bytes(response.body))


def test_typed_problem_renders_a_relative_type_with_headers_and_extensions() -> None:
    response = ProblemResponse(
        409,
        "purchase is still in progress",
        problem_type="in_progress",
        headers={"Retry-After": "2"},
        extensions={"invocation_id": 7},
    )

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["retry-after"] == "2"
    assert _body(response) == {
        "type": "/problems/in_progress",
        "status": 409,
        "detail": "purchase is still in progress",
        "invocation_id": 7,
    }


def test_problem_without_type_renders_about_blank() -> None:
    response = ProblemResponse(404, "something went wrong")

    assert response.headers["content-type"] == "application/problem+json"
    assert _body(response) == {
        "type": "about:blank",
        "status": 404,
        "detail": "something went wrong",
    }


def test_extensions_never_replace_standard_members() -> None:
    response = ProblemResponse(
        409,
        "real detail",
        problem_type="conflict",
        extensions={"type": "spoofed", "status": 200, "detail": "spoofed"},
    )

    assert _body(response) == {
        "type": "/problems/conflict",
        "status": 409,
        "detail": "real detail",
    }
