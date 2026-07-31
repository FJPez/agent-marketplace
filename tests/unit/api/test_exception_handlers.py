import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from app.api.exception_handlers import (
    ExceptionHandlerConfig,
    _build_handler,
    _fixed_detail,
    install_exception_handlers,
)
from app.core.errors import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
)
from app.services.account_service import AccountNotFoundError


class _ChildNotFoundError(NotFoundError):
    pass


def _build_app(exc: Exception) -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/boom")
    async def raise_exception() -> dict[str, str]:
        raise exc

    return app


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_detail"),
    [
        (NotFoundError("thing missing"), status.HTTP_404_NOT_FOUND, "thing missing"),
        (ConflictError("conflicting change"), status.HTTP_409_CONFLICT, "conflicting change"),
        (PermissionDeniedError("not allowed"), status.HTTP_403_FORBIDDEN, "not allowed"),
        (InvalidStateError("wrong state"), status.HTTP_409_CONFLICT, "wrong state"),
    ],
)
def test_base_taxonomy_exceptions_translate_to_http(
    exc: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    client = TestClient(_build_app(exc))

    response = client.get("/boom")

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}


def test_unregistered_subclass_falls_back_to_base_handler() -> None:
    client = TestClient(_build_app(_ChildNotFoundError("child missing")))

    response = client.get("/boom")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "child missing"}


def test_specific_registration_beats_base_fallback() -> None:
    app = _build_app(_ChildNotFoundError("child missing"))
    app.add_exception_handler(
        _ChildNotFoundError,
        _build_handler(
            ExceptionHandlerConfig(
                status_code=status.HTTP_404_NOT_FOUND,
                detail_builder=_fixed_detail("redacted"),
            ),
        ),
    )
    client = TestClient(app)

    response = client.get("/boom")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "redacted"}


def test_existing_specific_registration_is_undisturbed() -> None:
    client = TestClient(_build_app(AccountNotFoundError("secret internals")))

    response = client.get("/boom")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "account not found"}
