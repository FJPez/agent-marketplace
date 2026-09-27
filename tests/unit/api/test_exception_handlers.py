import pytest
from fastapi import Request, status
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from tests.unit.api.conftest import AppFactory

from app.core.errors import (
    ConflictError,
    InvalidInputError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
    UnavailableError,
    UpstreamError,
    UpstreamTimeoutError,
)
from app.services.health_service import ReadinessCheckError


class ChildNotFoundError(NotFoundError):
    pass


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_type", "expected_title"),
    [
        (NotFoundError("boom"), status.HTTP_404_NOT_FOUND, "/problems/not_found", "Not found"),
        (
            ChildNotFoundError("boom"),
            status.HTTP_404_NOT_FOUND,
            "/problems/not_found",
            "Not found",
        ),
        (
            UnauthenticatedError("boom"),
            status.HTTP_401_UNAUTHORIZED,
            "/problems/unauthenticated",
            "Unauthenticated",
        ),
        (ConflictError("boom"), status.HTTP_409_CONFLICT, "/problems/conflict", "Conflict"),
        (
            InvalidInputError("boom"),
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "/problems/invalid_input",
            "Invalid input",
        ),
        (
            PermissionDeniedError("boom"),
            status.HTTP_403_FORBIDDEN,
            "/problems/permission_denied",
            "Permission denied",
        ),
        (
            InvalidStateError("boom"),
            status.HTTP_409_CONFLICT,
            "/problems/invalid_state",
            "Invalid state",
        ),
        (
            UpstreamError("boom"),
            status.HTTP_502_BAD_GATEWAY,
            "/problems/upstream_error",
            "Upstream error",
        ),
        (
            UpstreamTimeoutError("boom"),
            status.HTTP_504_GATEWAY_TIMEOUT,
            "/problems/upstream_timeout",
            "Upstream timeout",
        ),
        (
            UnavailableError("boom"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "/problems/unavailable",
            "Unavailable",
        ),
        (
            ReadinessCheckError("boom"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "/problems/not_ready",
            "Not ready",
        ),
    ],
)
def test_mapped_exceptions_render_problem_details(
    handler_app_factory: AppFactory,
    exc: Exception,
    expected_status: int,
    expected_type: str,
    expected_title: str,
) -> None:
    client = TestClient(handler_app_factory(exc))

    response = client.get("/boom")

    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": expected_type,
        "title": expected_title,
        "status": expected_status,
        "detail": "boom",
    }


def test_application_error_carries_problem_type_headers_and_extensions(
    handler_app_factory: AppFactory,
) -> None:
    exc = ConflictError(
        "purchase is still in progress",
        problem_type="in_progress",
        headers={"Retry-After": "2"},
        extensions={"invocation_id": 7},
    )
    client = TestClient(handler_app_factory(exc))

    response = client.get("/boom")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["retry-after"] == "2"
    assert response.json() == {
        "type": "/problems/in_progress",
        "title": "In progress",
        "status": 409,
        "detail": "purchase is still in progress",
        "invocation_id": 7,
    }


def test_specific_registration_beats_base_fallback(
    handler_app_factory: AppFactory,
) -> None:
    async def redacted_handler(request: Request, exc: Exception) -> Response:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "redacted"},
        )

    app = handler_app_factory(ChildNotFoundError("child missing"))
    app.add_exception_handler(ChildNotFoundError, redacted_handler)
    client = TestClient(app)

    response = client.get("/boom")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "redacted"}
