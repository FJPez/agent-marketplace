import pytest
from fastapi import status
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


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_type"),
    [
        (NotFoundError("boom"), 404, "/problems/not_found"),
        (UnauthenticatedError("boom"), 401, "/problems/unauthenticated"),
        (ConflictError("boom"), 409, "/problems/conflict"),
        (InvalidInputError("boom"), 422, "/problems/invalid_input"),
        (PermissionDeniedError("boom"), 403, "/problems/permission_denied"),
        (InvalidStateError("boom"), 409, "/problems/invalid_state"),
        (UpstreamError("boom"), 502, "/problems/upstream_error"),
        (UpstreamTimeoutError("boom"), 504, "/problems/upstream_timeout"),
        (UnavailableError("boom"), 503, "/problems/unavailable"),
        (ReadinessCheckError("boom"), 503, "/problems/not_ready"),
    ],
)
def test_application_errors_render_their_status_and_type(
    handler_app_factory: AppFactory,
    exc: Exception,
    expected_status: int,
    expected_type: str,
) -> None:
    client = TestClient(handler_app_factory(exc))

    response = client.get("/boom")

    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": expected_type,
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
        "status": 409,
        "detail": "purchase is still in progress",
        "invocation_id": 7,
    }
