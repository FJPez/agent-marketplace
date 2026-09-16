from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_consumer_account_record,
    create_endpoint_record,
    create_invocation_record,
    create_moderation_action_record,
    create_provider_account_record,
    create_quote_record,
    create_service_record,
    create_upstream_record,
)

from app.core.enums import AccessMode, InvocationFailureReason, InvocationStatus
from app.core.errors import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    UpstreamError,
    UpstreamTimeoutError,
)
from app.db.models import Invocation, ServiceEndpoint
from app.services import invoke

pytestmark = [pytest.mark.asyncio]

PAYLOAD: dict[str, object] = {"text": "hello"}


class FakeHttpClient:
    """Stands in for the outbound http client, the invoke path's only external I/O."""

    def __init__(self, outcomes: list[Response | Exception] | None = None) -> None:
        self.outcomes: list[Response | Exception] = [] if outcomes is None else outcomes
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
        if not self.outcomes:
            raise AssertionError("no fake outcome configured")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class InvokeTarget:
    consumer_account_id: int
    service_id: int
    endpoint_id: int


async def seed_target(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    slug: str,
    access_mode: AccessMode = AccessMode.FREE,
    is_enabled: bool = True,
    with_upstream: bool = True,
    upstream_config: dict[str, object] | None = None,
    base_url: str = "http://127.0.0.1:9000",
    request_schema: dict[str, object] | None = None,
) -> InvokeTarget:
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
        access_mode=access_mode,
        is_enabled=is_enabled,
        request_schema=request_schema,
    )
    if with_upstream:
        await create_upstream_record(
            db_session_factory,
            endpoint_id=endpoint_id,
            base_url=base_url,
            config=upstream_config,
        )
    return InvokeTarget(
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
    )


async def read_invocation(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    idempotency_key: str,
) -> Invocation:
    async with db_session_factory() as session:
        invocation = await session.scalar(
            select(Invocation).where(Invocation.idempotency_key == idempotency_key),
        )
    assert invocation is not None
    return invocation


async def disable_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
) -> None:
    async with db_session_factory.begin() as session:
        endpoint = await session.get(ServiceEndpoint, endpoint_id)
        assert endpoint is not None
        endpoint.is_enabled = False


async def resolve(
    session: AsyncSession,
    *,
    target: InvokeTarget,
    payload: object = PAYLOAD,
    quote_id: int | None = None,
) -> invoke.ResolvedInvokeTarget:
    return await invoke.resolve_target(
        session=session,
        service_ref=target.service_id,
        endpoint_key="translate",
        payload=payload,
        quote_id=quote_id,
    )


async def replay(
    session: AsyncSession,
    *,
    target: InvokeTarget,
    idempotency_key: str,
    payload: object = PAYLOAD,
    quote_id: int | None = None,
) -> Invocation | None:
    return await invoke.try_replay(
        session=session,
        account_id=target.consumer_account_id,
        service_ref=target.service_id,
        endpoint_key="translate",
        payload=payload,
        quote_id=quote_id,
        idempotency_key=idempotency_key,
    )


async def test_resolve_target_returns_the_enabled_endpoint_and_its_auth(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="resolve-happy")

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)

    assert resolved.service.id == target.service_id
    assert resolved.endpoint.id == target.endpoint_id
    assert resolved.quote is None
    assert resolved.auth.key_id == "gateway-key"
    assert len(resolved.request_hash) == 64


async def test_resolve_target_rejects_an_unknown_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="resolve-unknown")

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError, match="service not found"):
            await invoke.resolve_target(
                session=session,
                service_ref=target.service_id + 1000,
                endpoint_key="translate",
                payload=PAYLOAD,
                quote_id=None,
            )


async def test_resolve_target_hides_a_suspended_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="resolve-suspended")
    await create_moderation_action_record(
        db_session_factory,
        service_id=target.service_id,
        action="suspend",
    )

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError, match="service not found"):
            await resolve(session, target=target)


async def test_resolve_target_hides_a_disabled_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="resolve-disabled", is_enabled=False)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError, match="endpoint not found"):
            await resolve(session, target=target)


async def test_resolve_target_rejects_an_unknown_endpoint_key(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="resolve-unknown-endpoint")

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError, match="endpoint not found"):
            await invoke.resolve_target(
                session=session,
                service_ref=target.service_id,
                endpoint_key="missing",
                payload=PAYLOAD,
                quote_id=None,
            )


async def test_resolve_target_rejects_an_endpoint_without_an_upstream(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(
        db_session_factory,
        slug="resolve-no-upstream",
        with_upstream=False,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError, match="service endpoint is not invokable"):
            await resolve(session, target=target)


async def test_resolve_target_rejects_an_upstream_without_hmac_auth(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(
        db_session_factory,
        slug="resolve-no-auth",
        upstream_config={"auth": {"type": "none"}},
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError, match="service endpoint is not invokable"):
            await resolve(session, target=target)


async def test_resolve_target_rejects_a_payload_the_endpoint_schema_refuses(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(
        db_session_factory,
        slug="resolve-schema",
        request_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )

    async with db_session_factory() as session:
        with pytest.raises(ConflictError, match="request payload does not match endpoint schema"):
            await resolve(session, target=target, payload={"text": 123})


async def test_resolve_target_rejects_a_quote_issued_for_another_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="resolve-quote")
    other = await seed_target(db_session_factory, slug="resolve-quote-other")
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=other.service_id,
        endpoint_id=other.endpoint_id,
        payload=PAYLOAD,
    )

    async with db_session_factory() as session:
        with pytest.raises(ConflictError, match="quote is not valid for invoke"):
            await resolve(session, target=target, quote_id=quote_id)


async def test_try_replay_returns_a_stored_success(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="replay-success")
    invocation_id = await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload=PAYLOAD,
        idempotency_key="replay-stored-success",
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "cached"},
    )

    async with db_session_factory() as session:
        replayed = await replay(session, target=target, idempotency_key="replay-stored-success")

    assert replayed is not None
    assert replayed.id == invocation_id


async def test_try_replay_returns_nothing_when_no_invocation_is_stored(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="replay-missing")

    async with db_session_factory() as session:
        replayed = await replay(session, target=target, idempotency_key="replay-nothing")

    assert replayed is None


async def test_try_replay_raises_a_stored_failure_after_the_endpoint_is_disabled(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="replay-failure-disabled")
    await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload=PAYLOAD,
        idempotency_key="replay-failure-disabled",
        status=InvocationStatus.FAILED,
        response_payload=None,
        upstream_status_code=None,
        error_message="upstream request timed out",
        failure_reason=InvocationFailureReason.UPSTREAM_TIMEOUT,
    )
    await disable_endpoint(db_session_factory, endpoint_id=target.endpoint_id)

    async with db_session_factory() as session:
        with pytest.raises(UpstreamTimeoutError, match="upstream request timed out"):
            await replay(session, target=target, idempotency_key="replay-failure-disabled")


async def test_try_replay_raises_a_stored_failure_after_its_quote_expires(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="replay-failure-expired-quote")
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload=PAYLOAD,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        quote_id=quote_id,
        payload=PAYLOAD,
        idempotency_key="replay-failure-expired-quote",
        status=InvocationStatus.FAILED,
        response_payload=None,
        upstream_status_code=500,
        error_message="upstream returned an error response",
        failure_reason=InvocationFailureReason.UPSTREAM_RESPONSE,
    )

    async with db_session_factory() as session:
        with pytest.raises(UpstreamError, match="upstream returned an error response"):
            await replay(
                session,
                target=target,
                idempotency_key="replay-failure-expired-quote",
                quote_id=quote_id,
            )


async def test_try_replay_reports_a_live_lease_after_its_quote_expires(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="replay-live-lease-expired-quote")
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload=PAYLOAD,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        quote_id=quote_id,
        payload=PAYLOAD,
        idempotency_key="replay-live-lease",
        status=InvocationStatus.IN_PROGRESS,
        upstream_status_code=None,
        in_progress_until=datetime.now(UTC) + timedelta(seconds=60),
    )

    async with db_session_factory() as session:
        with pytest.raises(ConflictError, match="request already in progress"):
            await replay(
                session,
                target=target,
                idempotency_key="replay-live-lease",
                quote_id=quote_id,
            )


async def test_try_replay_requires_recovery_after_the_service_is_delisted(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="replay-null-lease-delisted")
    await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload=PAYLOAD,
        idempotency_key="replay-null-lease",
        status=InvocationStatus.IN_PROGRESS,
        upstream_status_code=None,
        in_progress_until=None,
    )
    await create_moderation_action_record(
        db_session_factory,
        service_id=target.service_id,
        action="delist",
    )

    async with db_session_factory() as session:
        with pytest.raises(ConflictError, match="invocation outcome is unknown; recovery required"):
            await replay(session, target=target, idempotency_key="replay-null-lease")


async def test_try_replay_rejects_a_key_reused_for_a_different_request(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="replay-identity")
    await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload={"text": "something else"},
        idempotency_key="replay-reused-key",
        status=InvocationStatus.FAILED,
        response_payload=None,
        upstream_status_code=None,
        error_message="upstream request timed out",
        failure_reason=InvocationFailureReason.UPSTREAM_TIMEOUT,
    )

    async with db_session_factory() as session:
        with pytest.raises(
            ConflictError,
            match="idempotency key already used for a different request",
        ):
            await replay(session, target=target, idempotency_key="replay-reused-key")


async def test_execute_persists_a_successful_invocation_and_clears_the_lease(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="execute-success")
    http_client = FakeHttpClient([Response(status_code=200, json={"result": "bonjour"})])

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)
        await invoke.execute(
            session=session,
            account_id=target.consumer_account_id,
            resolved=resolved,
            idempotency_key="execute-success",
            http_client=http_client,
        )

    persisted = await read_invocation(db_session_factory, idempotency_key="execute-success")

    assert persisted.status is InvocationStatus.SUCCEEDED
    assert persisted.response_payload == {"result": "bonjour"}
    assert persisted.upstream_status_code == 200
    assert persisted.error_message is None
    assert persisted.failure_reason is None
    assert persisted.in_progress_until is None
    assert len(http_client.calls) == 1


async def test_execute_records_an_unsafe_upstream_target_as_a_transport_failure(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(
        db_session_factory,
        slug="execute-target",
        base_url="http://198.51.100.10:9000",
    )
    http_client = FakeHttpClient()

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)
        with pytest.raises(UpstreamError, match="upstream target is not allowed"):
            await invoke.execute(
                session=session,
                account_id=target.consumer_account_id,
                resolved=resolved,
                idempotency_key="execute-target",
                http_client=http_client,
            )

    persisted = await read_invocation(db_session_factory, idempotency_key="execute-target")

    assert persisted.status is InvocationStatus.FAILED
    assert persisted.failure_reason is InvocationFailureReason.UPSTREAM_TRANSPORT
    assert persisted.error_message == "upstream target is not allowed"
    assert persisted.upstream_status_code is None
    assert persisted.in_progress_until is None
    assert http_client.calls == []


async def test_execute_records_an_upstream_timeout(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="execute-timeout")
    http_client = FakeHttpClient([httpx.TimeoutException("boom")])

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)
        with pytest.raises(UpstreamTimeoutError, match="upstream request timed out"):
            await invoke.execute(
                session=session,
                account_id=target.consumer_account_id,
                resolved=resolved,
                idempotency_key="execute-timeout",
                http_client=http_client,
            )

    persisted = await read_invocation(db_session_factory, idempotency_key="execute-timeout")

    assert persisted.status is InvocationStatus.FAILED
    assert persisted.failure_reason is InvocationFailureReason.UPSTREAM_TIMEOUT
    assert persisted.error_message == "upstream request timed out"
    assert persisted.upstream_status_code is None
    assert persisted.in_progress_until is None


async def test_execute_records_an_upstream_transport_failure(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="execute-transport")
    http_client = FakeHttpClient([httpx.ConnectError("refused")])

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)
        with pytest.raises(UpstreamError, match="upstream request failed"):
            await invoke.execute(
                session=session,
                account_id=target.consumer_account_id,
                resolved=resolved,
                idempotency_key="execute-transport",
                http_client=http_client,
            )

    persisted = await read_invocation(db_session_factory, idempotency_key="execute-transport")

    assert persisted.status is InvocationStatus.FAILED
    assert persisted.failure_reason is InvocationFailureReason.UPSTREAM_TRANSPORT
    assert persisted.error_message == "upstream request failed"
    assert persisted.upstream_status_code is None
    assert persisted.in_progress_until is None


async def test_execute_records_an_upstream_error_response_with_its_status_code(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="execute-response")
    http_client = FakeHttpClient([Response(status_code=503, json={"detail": "down"})])

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)
        with pytest.raises(UpstreamError, match="upstream request failed"):
            await invoke.execute(
                session=session,
                account_id=target.consumer_account_id,
                resolved=resolved,
                idempotency_key="execute-response",
                http_client=http_client,
            )

    persisted = await read_invocation(db_session_factory, idempotency_key="execute-response")

    assert persisted.status is InvocationStatus.FAILED
    assert persisted.failure_reason is InvocationFailureReason.UPSTREAM_RESPONSE
    assert persisted.error_message == "upstream request failed"
    assert persisted.upstream_status_code == 503
    assert persisted.in_progress_until is None


async def test_execute_replays_a_succeeded_invocation_without_calling_upstream(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="execute-replay-success")
    invocation_id = await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload=PAYLOAD,
        idempotency_key="replay-success",
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "cached"},
    )
    http_client = FakeHttpClient()

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)
        replayed = await invoke.execute(
            session=session,
            account_id=target.consumer_account_id,
            resolved=resolved,
            idempotency_key="replay-success",
            http_client=http_client,
        )

        assert replayed.id == invocation_id
        assert replayed.response_payload == {"result": "cached"}

    assert http_client.calls == []


async def test_execute_replays_a_failed_invocation_without_calling_upstream(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="execute-replay-failure")
    await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload=PAYLOAD,
        idempotency_key="replay-failure",
        status=InvocationStatus.FAILED,
        response_payload=None,
        upstream_status_code=None,
        error_message="upstream request timed out",
        failure_reason=InvocationFailureReason.UPSTREAM_TIMEOUT,
    )
    http_client = FakeHttpClient()

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)
        with pytest.raises(UpstreamTimeoutError, match="upstream request timed out"):
            await invoke.execute(
                session=session,
                account_id=target.consumer_account_id,
                resolved=resolved,
                idempotency_key="replay-failure",
                http_client=http_client,
            )

    assert http_client.calls == []


async def test_execute_rejects_a_reused_key_before_returning_any_stored_outcome(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="execute-identity")
    await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload={"text": "something else"},
        idempotency_key="reused-key",
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "cached"},
    )
    http_client = FakeHttpClient()

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)
        with pytest.raises(
            ConflictError,
            match="idempotency key already used for a different request",
        ):
            await invoke.execute(
                session=session,
                account_id=target.consumer_account_id,
                resolved=resolved,
                idempotency_key="reused-key",
                http_client=http_client,
            )

    assert http_client.calls == []


async def test_execute_rejects_a_request_whose_lease_is_still_live(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="execute-live-lease")
    await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload=PAYLOAD,
        idempotency_key="live-lease",
        status=InvocationStatus.IN_PROGRESS,
        upstream_status_code=None,
        in_progress_until=datetime.now(UTC) + timedelta(seconds=60),
    )
    http_client = FakeHttpClient()

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)
        with pytest.raises(ConflictError, match="request already in progress"):
            await invoke.execute(
                session=session,
                account_id=target.consumer_account_id,
                resolved=resolved,
                idempotency_key="live-lease",
                http_client=http_client,
            )

    assert http_client.calls == []


async def test_execute_requires_recovery_when_an_in_progress_row_carries_no_lease(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="execute-null-lease")
    await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload=PAYLOAD,
        idempotency_key="null-lease",
        status=InvocationStatus.IN_PROGRESS,
        upstream_status_code=None,
        in_progress_until=None,
    )
    http_client = FakeHttpClient()

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)
        with pytest.raises(ConflictError, match="invocation outcome is unknown; recovery required"):
            await invoke.execute(
                session=session,
                account_id=target.consumer_account_id,
                resolved=resolved,
                idempotency_key="null-lease",
                http_client=http_client,
            )

    assert http_client.calls == []


async def test_execute_never_re_forwards_an_expired_lease(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="execute-expired-lease")
    await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload=PAYLOAD,
        idempotency_key="expired-lease",
        status=InvocationStatus.IN_PROGRESS,
        upstream_status_code=None,
        in_progress_until=datetime.now(UTC) - timedelta(seconds=60),
    )
    http_client = FakeHttpClient([Response(status_code=200, json={"result": "fresh"})])

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)
        with pytest.raises(ConflictError, match="invocation outcome is unknown; recovery required"):
            await invoke.execute(
                session=session,
                account_id=target.consumer_account_id,
                resolved=resolved,
                idempotency_key="expired-lease",
                http_client=http_client,
            )

    persisted = await read_invocation(db_session_factory, idempotency_key="expired-lease")

    assert persisted.status is InvocationStatus.IN_PROGRESS
    assert http_client.calls == []


async def test_execute_leaves_the_row_claimed_when_the_client_fails_unexpectedly(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="execute-unexpected")
    http_client = FakeHttpClient([RuntimeError("client exploded")])

    async with db_session_factory() as session:
        resolved = await resolve(session, target=target)
        with pytest.raises(RuntimeError, match="client exploded"):
            await invoke.execute(
                session=session,
                account_id=target.consumer_account_id,
                resolved=resolved,
                idempotency_key="unexpected",
                http_client=http_client,
            )

    persisted = await read_invocation(db_session_factory, idempotency_key="unexpected")

    assert persisted.status is InvocationStatus.IN_PROGRESS
    assert persisted.in_progress_until is not None
    assert len(http_client.calls) == 1
