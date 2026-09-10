import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_consumer_account_record,
    create_endpoint_record,
    create_provider_account_record,
    create_service_record,
    create_upstream_record,
)

from app.core.enums import InvocationStatus
from app.core.errors import ConflictError
from app.db.models import Invocation
from app.services import invoke

pytestmark = [pytest.mark.asyncio]

PAYLOAD = {"text": "hello"}
IDEMPOTENCY_KEY = "concurrent-key"


@pytest.fixture(autouse=True)
async def clear_invocations(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    _ = migrated_database
    yield
    # Requesting migrated_database orders this cleanup before its downgrade, which
    # narrows the status CHECK back to the terminal values that in-progress rows fail.
    async with db_session_factory.begin() as session:
        await session.execute(delete(Invocation))


class GatedHttpClient:
    """Holds the first upstream call open so a second caller meets a live lease."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[str] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: object,
        headers: dict[str, str],
        **kwargs: object,
    ) -> Response:
        _ = json
        _ = headers
        _ = kwargs
        self.calls.append(f"{method} {url}")
        self.started.set()
        await self.release.wait()
        return Response(status_code=200, json={"result": "bonjour"})

    async def aclose(self) -> None:
        return None


async def seed_target(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    slug: str,
) -> tuple[int, int]:
    provider_account_id = await create_provider_account_record(db_session_factory)
    consumer_account_id = await create_consumer_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug=slug,
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
    )
    await create_upstream_record(db_session_factory, endpoint_id=endpoint_id)
    return consumer_account_id, service_id


async def run_invoke(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    account_id: int,
    service_id: int,
    http_client: GatedHttpClient,
) -> Invocation:
    async with db_session_factory() as session:
        resolved = await invoke.resolve_target(
            session=session,
            service_ref=service_id,
            endpoint_key="translate",
            payload=PAYLOAD,
            quote_id=None,
        )
        return await invoke.execute(
            session=session,
            account_id=account_id,
            resolved=resolved,
            idempotency_key=IDEMPOTENCY_KEY,
            http_client=http_client,
        )


async def count_invocations(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    account_id: int,
) -> int:
    async with db_session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Invocation)
            .where(Invocation.consumer_account_id == account_id),
        )
    assert count is not None
    return count


async def test_a_second_request_meeting_a_live_lease_is_rejected_without_a_second_forward(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, service_id = await seed_target(db_session_factory, slug="live-lease-race")
    http_client = GatedHttpClient()

    first = asyncio.create_task(
        run_invoke(
            db_session_factory,
            account_id=account_id,
            service_id=service_id,
            http_client=http_client,
        )
    )
    await http_client.started.wait()

    with pytest.raises(ConflictError, match="request already in progress"):
        await run_invoke(
            db_session_factory,
            account_id=account_id,
            service_id=service_id,
            http_client=http_client,
        )

    http_client.release.set()
    invocation = await first

    assert invocation.status is InvocationStatus.SUCCEEDED
    assert len(http_client.calls) == 1
    assert await count_invocations(db_session_factory, account_id=account_id) == 1


async def test_two_racing_claims_forward_exactly_once(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, service_id = await seed_target(db_session_factory, slug="insert-race")
    http_client = GatedHttpClient()

    tasks = [
        asyncio.create_task(
            run_invoke(
                db_session_factory,
                account_id=account_id,
                service_id=service_id,
                http_client=http_client,
            )
        )
        for _ in range(2)
    ]
    await http_client.started.wait()
    # The claim that lost the unique constraint settles while the winner is still
    # held inside the upstream call, so it completes first.
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    http_client.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    succeeded = [
        result
        for result in results
        if isinstance(result, Invocation) and result.status is InvocationStatus.SUCCEEDED
    ]
    rejected = [
        result
        for result in results
        if isinstance(result, ConflictError) and str(result) == "request already in progress"
    ]

    assert len(succeeded) == 1
    assert len(rejected) == 1
    assert len(http_client.calls) == 1
    assert await count_invocations(db_session_factory, account_id=account_id) == 1
