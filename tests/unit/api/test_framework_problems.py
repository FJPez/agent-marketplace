from http import HTTPStatus

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator

from app.api.exception_handlers import install_exception_handlers


class _Item(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def reject_blank_name(cls, value: str) -> str:
        if not value.strip():
            msg = "name must not be blank"
            raise ValueError(msg)
        return value


def _build_client() -> TestClient:
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/items")
    async def list_items(limit: int) -> dict[str, int]:
        return {"limit": limit}

    @app.post("/items")
    async def create_item(item: _Item) -> dict[str, str]:
        return {"name": item.name}

    @app.get("/teapot")
    async def brew() -> None:
        raise HTTPException(
            status_code=status.HTTP_418_IM_A_TEAPOT,
            detail="short and stout",
            headers={"X-Teapot": "yes"},
        )

    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path", "expected_status", "expected_detail", "expected_headers"),
    [
        ("GET", "/does-not-exist", 404, "Not Found", {}),
        ("POST", "/teapot", 405, "Method Not Allowed", {"allow": "GET"}),
        ("GET", "/teapot", 418, "short and stout", {"x-teapot": "yes"}),
    ],
    ids=["unknown_route", "method_not_allowed", "route_local_http_exception"],
)
def test_http_exceptions_render_about_blank_problems(
    method: str,
    path: str,
    expected_status: int,
    expected_detail: str,
    expected_headers: dict[str, str],
) -> None:
    response = _build_client().request(method, path)

    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
    assert expected_headers.items() <= response.headers.items()
    assert response.json() == {
        "type": "about:blank",
        "title": HTTPStatus(expected_status).phrase,
        "status": expected_status,
        "detail": expected_detail,
    }


def test_request_validation_error_lists_errors_as_extension() -> None:
    response = _build_client().get("/items", params={"limit": "many"})

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "/problems/invalid_input",
        "title": "Invalid input",
        "status": 422,
        "detail": "request validation failed; see errors",
        "errors": [
            {
                "type": "int_parsing",
                "loc": ["query", "limit"],
                "msg": "Input should be a valid integer, unable to parse string as an integer",
                "input": "many",
            },
        ],
    }


@pytest.mark.parametrize(
    ("body", "expected_error_type", "expected_error_loc"),
    [
        (b'{"name": ', "json_invalid", ["body", 9]),
        (b'{"name": "   "}', "value_error", ["body", "name"]),
    ],
    ids=["malformed_json", "validator_exception_in_context"],
)
def test_invalid_bodies_render_invalid_input_problems(
    body: bytes,
    expected_error_type: str,
    expected_error_loc: list[str | int],
) -> None:
    response = _build_client().post(
        "/items",
        content=body,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    problem = response.json()
    assert problem["type"] == "/problems/invalid_input"
    assert [(error["type"], error["loc"]) for error in problem["errors"]] == [
        (expected_error_type, expected_error_loc),
    ]
