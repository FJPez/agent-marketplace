from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def app() -> FastAPI:
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()
