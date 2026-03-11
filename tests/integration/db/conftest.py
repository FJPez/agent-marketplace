import asyncio
import os
from collections.abc import Generator

import pytest
from tests.integration.db.support import (
    drop_test_database,
    get_test_database_url,
    recreate_test_database,
)

from app.core.config import Settings, get_settings


@pytest.fixture(scope="session", autouse=True)
def use_dedicated_test_database() -> Generator[None, None, None]:
    original_database_url = os.environ.get("APP_DATABASE_URL")
    base_database_url = original_database_url or Settings().database_url
    test_database_url = get_test_database_url(base_database_url)
    get_settings.cache_clear()
    asyncio.run(recreate_test_database(test_database_url))
    os.environ["APP_DATABASE_URL"] = test_database_url
    get_settings.cache_clear()

    try:
        yield
    finally:
        asyncio.run(drop_test_database(test_database_url))
        if original_database_url is None:
            os.environ.pop("APP_DATABASE_URL", None)
        else:
            os.environ["APP_DATABASE_URL"] = original_database_url
        get_settings.cache_clear()
