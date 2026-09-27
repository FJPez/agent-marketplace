from collections.abc import Awaitable, Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.helpers.auth import api_key_headers_for_account, auth_headers_for_account, create_account

from app.api.deps.auth import CurrentActor
from app.api.deps.database import SessionDep


@pytest.mark.parametrize(
    "headers_for_account",
    [
        pytest.param(auth_headers_for_account, id="jwt"),
        pytest.param(api_key_headers_for_account, id="api_key"),
    ],
)
async def test_authentication_leaves_no_transaction_open_on_the_request_session(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    headers_for_account: Callable[..., Awaitable[dict[str, str]]],
) -> None:
    @app.get("/probe/request-session")
    async def probe(actor: CurrentActor, session: SessionDep) -> dict[str, int | bool]:
        return {"account_id": actor.account_id, "in_transaction": session.in_transaction()}

    account_id = await create_account(db_session_factory)
    headers = await headers_for_account(db_session_factory, account_id=account_id)

    # An API key's second request falls inside the touch interval, so it writes nothing.
    responses = [
        await async_client.get("/probe/request-session", headers=headers) for _ in range(2)
    ]

    assert [response.json() for response in responses] == [
        {"account_id": account_id, "in_transaction": False},
    ] * 2
