import pytest
from fastapi import Request, status
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from tests.unit.api.conftest import AppFactory

from app.core.errors import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)


class ChildNotFoundError(NotFoundError):
    pass


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_detail"),
    [
        (NotFoundError("thing missing"), status.HTTP_404_NOT_FOUND, "thing missing"),
        (
            UnauthenticatedError("credentials required"),
            status.HTTP_401_UNAUTHORIZED,
            "credentials required",
        ),
        (ConflictError("conflicting change"), status.HTTP_409_CONFLICT, "conflicting change"),
        (PermissionDeniedError("not allowed"), status.HTTP_403_FORBIDDEN, "not allowed"),
        (InvalidStateError("wrong state"), status.HTTP_409_CONFLICT, "wrong state"),
    ],
)
def test_base_taxonomy_exceptions_translate_to_http(
    handler_app_factory: AppFactory,
    exc: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    client = TestClient(handler_app_factory(exc))

    response = client.get("/boom")

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}


def test_unregistered_subclass_falls_back_to_base_handler(
    handler_app_factory: AppFactory,
) -> None:
    client = TestClient(handler_app_factory(ChildNotFoundError("child missing")))

    response = client.get("/boom")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "child missing"}


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
