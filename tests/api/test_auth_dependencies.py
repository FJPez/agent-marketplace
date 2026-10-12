from collections.abc import Awaitable, Callable, Iterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.helpers.auth import api_key_headers_for_account, auth_headers_for_account, create_account

from app.api.deps.auth import CurrentActor
from app.api.deps.database import SessionDep
from app.core.lifespan import get_resources

HeadersForAccount = Callable[..., Awaitable[dict[str, str]]]

credential_types = pytest.mark.parametrize(
    "headers_for_account",
    [
        pytest.param(auth_headers_for_account, id="jwt"),
        pytest.param(api_key_headers_for_account, id="api_key"),
    ],
)


@pytest.fixture
def executed_statements(app: FastAPI, async_client: AsyncClient) -> Iterator[list[str]]:
    """The SQL the app's own engine runs during the test."""
    statements: list[str] = []
    engine = get_resources(app).db_engine.sync_engine

    def record(*args: object) -> None:
        # before_cursor_execute passes (connection, cursor, statement, ...).
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", record)
    yield statements
    event.remove(engine, "before_cursor_execute", record)


@credential_types
async def test_authentication_leaves_no_transaction_open_on_the_request_session(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    headers_for_account: HeadersForAccount,
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


@credential_types
async def test_a_request_looks_its_account_up_once(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    executed_statements: list[str],
    headers_for_account: HeadersForAccount,
) -> None:
    account_id = await create_account(db_session_factory)
    headers = await headers_for_account(db_session_factory, account_id=account_id)

    # The rate limit and the route's own dependency both need the actor.
    response = await async_client.get("/v1/provider/services", headers=headers)

    assert response.status_code == 200
    account_lookups = [sql for sql in executed_statements if "FROM accounts" in sql]
    assert len(account_lookups) == 1


async def test_the_bearer_scheme_is_accepted_in_lower_case(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_account(db_session_factory)
    headers = await auth_headers_for_account(db_session_factory, account_id=account_id)
    token = headers["Authorization"].removeprefix("Bearer ")

    response = await async_client.get(
        "/v1/provider/services",
        headers={"Authorization": f"bearer {token}"},
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "authorization",
    [
        pytest.param("Basic dXNlcjpwYXNz", id="another_scheme"),
        pytest.param("Bearer not-a-token", id="invalid_token"),
    ],
)
async def test_a_protected_route_answers_401_without_a_valid_bearer_token(
    async_client: AsyncClient,
    authorization: str,
) -> None:
    response = await async_client.get(
        "/v1/provider/services",
        headers={"Authorization": authorization},
    )

    assert response.status_code == 401
    assert response.json() == {
        "type": "/problems/unauthenticated",
        "status": 401,
        "detail": "a valid bearer token is required",
    }


async def test_a_public_route_ignores_an_invalid_token(async_client: AsyncClient) -> None:
    response = await async_client.get(
        "/v1/services",
        headers={"Authorization": "Bearer not-a-token"},
    )

    assert response.status_code == 200
