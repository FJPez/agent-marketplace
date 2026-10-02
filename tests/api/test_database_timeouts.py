import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.helpers.auth import create_account

from app.api.deps.database import SessionDep
from app.db.models import Account

UNAVAILABLE_PROBLEM = {
    "type": "/problems/unavailable",
    "status": 503,
    "detail": "the database did not answer in time; retry shortly",
}


async def test_a_statement_timeout_answers_503_with_retry_after(
    app: FastAPI,
    async_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    @app.get("/probe/slow-statement")
    async def slow_statement(session: SessionDep) -> None:
        await session.execute(text("SET LOCAL statement_timeout = '50ms'"))
        await session.execute(text("SELECT pg_sleep(1)"))

    with caplog.at_level(logging.WARNING, logger="app.api.exception_handlers"):
        response = await async_client.get("/probe/slow-statement")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["retry-after"] == "1"
    assert response.json() == UNAVAILABLE_PROBLEM
    assert [
        record.message for record in caplog.records if record.name == "app.api.exception_handlers"
    ] == ["database timeout"]


async def test_a_lock_timeout_answers_503_with_retry_after(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_account(db_session_factory)
    lock_account = select(Account.id).where(Account.id == account_id).with_for_update()

    @app.get("/probe/locked-row")
    async def locked_row(session: SessionDep) -> None:
        await session.execute(text("SET LOCAL lock_timeout = '50ms'"))
        await session.execute(lock_account)

    async with db_session_factory() as lock_holder:
        await lock_holder.execute(lock_account)
        response = await async_client.get("/probe/locked-row")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json() == UNAVAILABLE_PROBLEM


def test_other_database_errors_stay_internal_errors(app: FastAPI) -> None:
    @app.get("/probe/division-by-zero")
    async def division_by_zero(session: SessionDep) -> None:
        await session.execute(text("SELECT 1 / 0"))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/probe/division-by-zero")

    assert response.status_code == 500
    assert response.json()["type"] == "/problems/internal_error"
