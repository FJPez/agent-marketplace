from collections.abc import Callable
from typing import TYPE_CHECKING, cast

import pytest
from httpx import Response
from sqlalchemy.exc import IntegrityError

from app.core.actor import ActorContext
from app.core.enums import AccessMode, InvocationFailureReason, InvocationStatus, ServiceLifecycle
from app.db.models import Invocation, ProviderUpstream, Service, ServiceEndpoint
from app.integrations.provider_gateway.signing import HmacAuthConfig
from app.services.invoke_service import (
    InvokeGatewayTimeoutError,
    InvokeService,
    ResolvedInvokeTarget,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class FakeSession:
    def __init__(self, *, raise_integrity_on_commit: bool = False) -> None:
        self.flushes = 0
        self.commits = 0
        self.refreshes = 0
        self.rollbacks = 0
        self.raise_integrity_on_commit = raise_integrity_on_commit

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1
        if self.raise_integrity_on_commit and self.commits == 1:
            raise IntegrityError("statement", {}, Exception("duplicate invocation"))

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, instance: object) -> None:
        _ = instance
        self.refreshes += 1


class FakeInvocationRepository:
    def __init__(self, existing: Invocation) -> None:
        self.existing = existing
        self.lookup_count = 0
        self.add_calls = 0

    def add(
        self,
        *,
        consumer_account_id: int,
        service_id: int,
        endpoint_id: int,
        endpoint_key: str,
        access_mode: AccessMode,
        quote_id: int | None,
        idempotency_key: str,
        request_hash: str,
        status: InvocationStatus,
        response_payload: object | None,
        upstream_status_code: int | None,
        error_message: str | None,
        failure_reason: InvocationFailureReason | None,
    ) -> Invocation:
        _ = consumer_account_id
        _ = service_id
        _ = endpoint_id
        _ = endpoint_key
        _ = access_mode
        _ = quote_id
        _ = idempotency_key
        _ = request_hash
        _ = status
        _ = response_payload
        _ = upstream_status_code
        _ = error_message
        _ = failure_reason
        self.add_calls += 1
        return Invocation(
            id=777,
            consumer_account_id=self.existing.consumer_account_id,
            service_id=self.existing.service_id,
            endpoint_id=self.existing.endpoint_id,
            endpoint_key=self.existing.endpoint_key,
            access_mode=self.existing.access_mode,
            quote_id=self.existing.quote_id,
            idempotency_key=self.existing.idempotency_key,
            request_hash=self.existing.request_hash,
            status=InvocationStatus.FAILED,
            response_payload=None,
            upstream_status_code=None,
            error_message=None,
            failure_reason=None,
        )

    async def get_by_idempotency_key(
        self,
        *,
        consumer_account_id: int,
        idempotency_key: str,
    ) -> Invocation | None:
        assert consumer_account_id == self.existing.consumer_account_id
        assert idempotency_key == self.existing.idempotency_key
        self.lookup_count += 1
        if self.lookup_count == 1:
            return None
        return self.existing


class FakeSuccessSession:
    def __init__(self) -> None:
        self.flushes = 0
        self.commits = 0
        self.refreshes = 0

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, instance: object) -> None:
        _ = instance
        self.refreshes += 1


class FakeCommitSequenceSession:
    def __init__(
        self,
        *,
        fail_on_commit_calls: set[int] | None = None,
        on_successful_commit: Callable[[], None] | None = None,
    ) -> None:
        self.flushes = 0
        self.commits = 0
        self.refreshes = 0
        self.fail_on_commit_calls = fail_on_commit_calls or set()
        self.on_successful_commit = on_successful_commit

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1
        if self.commits in self.fail_on_commit_calls:
            raise RuntimeError("commit failed")
        if self.on_successful_commit is not None:
            self.on_successful_commit()

    async def refresh(self, instance: object) -> None:
        _ = instance
        self.refreshes += 1


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls = 0

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: object,
        headers: dict[str, str],
        **kwargs: object,
    ) -> Response:
        _ = method
        _ = url
        _ = json
        _ = headers
        _ = kwargs
        self.calls += 1
        return Response(status_code=200, json={"result": "bonjour"})

    async def aclose(self) -> None:
        return None


class FakeIdempotentHttpClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.side_effect_invocation_ids: set[str] = set()

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: object,
        headers: dict[str, str],
        **kwargs: object,
    ) -> Response:
        _ = method
        _ = url
        _ = json
        _ = kwargs
        invocation_id = headers["X-Agent-Marketplace-Invocation-Id"]
        self.calls.append(invocation_id)
        self.side_effect_invocation_ids.add(invocation_id)
        return Response(status_code=200, json={"result": "bonjour"})

    async def aclose(self) -> None:
        return None


class FakeNewInvocationRepository:
    def __init__(self) -> None:
        self.add_calls = 0

    async def get_by_idempotency_key(
        self,
        *,
        consumer_account_id: int,
        idempotency_key: str,
    ) -> Invocation | None:
        _ = consumer_account_id
        _ = idempotency_key
        return None

    def add(
        self,
        *,
        consumer_account_id: int,
        service_id: int,
        endpoint_id: int,
        endpoint_key: str,
        access_mode: AccessMode,
        quote_id: int | None,
        idempotency_key: str,
        request_hash: str,
        status: InvocationStatus,
        response_payload: object | None,
        upstream_status_code: int | None,
        error_message: str | None,
        failure_reason: InvocationFailureReason | None,
    ) -> Invocation:
        _ = failure_reason
        self.add_calls += 1
        return Invocation(
            id=505,
            consumer_account_id=consumer_account_id,
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key=endpoint_key,
            access_mode=access_mode,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status=status,
            response_payload=response_payload,
            upstream_status_code=upstream_status_code,
            error_message=error_message,
            failure_reason=None,
        )


class FakePersistedInvocationRepository:
    def __init__(self) -> None:
        self.add_calls = 0
        self.working_invocation: Invocation | None = None
        self.stored_invocation: Invocation | None = None

    async def get_by_idempotency_key(
        self,
        *,
        consumer_account_id: int,
        idempotency_key: str,
    ) -> Invocation | None:
        _ = consumer_account_id
        _ = idempotency_key
        return self.stored_invocation

    def add(
        self,
        *,
        consumer_account_id: int,
        service_id: int,
        endpoint_id: int,
        endpoint_key: str,
        access_mode: AccessMode,
        quote_id: int | None,
        idempotency_key: str,
        request_hash: str,
        status: InvocationStatus,
        response_payload: object | None,
        upstream_status_code: int | None,
        error_message: str | None,
        failure_reason: InvocationFailureReason | None,
    ) -> Invocation:
        self.add_calls += 1
        self.working_invocation = Invocation(
            id=505,
            consumer_account_id=consumer_account_id,
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key=endpoint_key,
            access_mode=access_mode,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status=status,
            response_payload=response_payload,
            upstream_status_code=upstream_status_code,
            error_message=error_message,
            failure_reason=failure_reason,
        )
        self.stored_invocation = Invocation(
            id=505,
            consumer_account_id=consumer_account_id,
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key=endpoint_key,
            access_mode=access_mode,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status=status,
            response_payload=response_payload,
            upstream_status_code=upstream_status_code,
            error_message=error_message,
            failure_reason=failure_reason,
        )
        return self.working_invocation

    def persist(self) -> None:
        assert self.working_invocation is not None
        assert self.stored_invocation is not None
        self.stored_invocation.status = self.working_invocation.status
        self.stored_invocation.response_payload = self.working_invocation.response_payload
        self.stored_invocation.upstream_status_code = self.working_invocation.upstream_status_code
        self.stored_invocation.error_message = self.working_invocation.error_message
        self.stored_invocation.failure_reason = self.working_invocation.failure_reason


def _resolved_target(*, auth: HmacAuthConfig | None = None) -> ResolvedInvokeTarget:
    service = Service(
        id=101,
        provider_account_id=1,
        slug="invoke-service",
        name="Invoke Service",
        summary="Summary",
        description=None,
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    endpoint = ServiceEndpoint(
        id=303,
        service_id=service.id,
        key="translate",
        name="Translate",
        summary=None,
        description=None,
        access_mode=AccessMode.FREE,
        request_schema={"type": "object"},
        response_schema={"type": "object"},
        timeout_seconds=30,
        is_enabled=True,
    )
    endpoint.upstream = ProviderUpstream(
        endpoint_id=endpoint.id,
        base_url="http://127.0.0.1:9000",
        path="/invoke",
        http_method="POST",
        config={
            "auth": {
                "type": "hmac_sha256",
                "key_id": "gateway-key",
                "secret": "super-secret",
            },
        },
    )
    service.endpoints = [endpoint]
    return ResolvedInvokeTarget(
        service=service,
        endpoint=endpoint,
        request_hash="a" * 64,
        quote=None,
        auth=auth or HmacAuthConfig(key_id="gateway-key", secret="super-secret"),
        payload={"text": "hello"},
    )


@pytest.mark.asyncio
async def test_execute_replays_existing_invocation_when_commit_hits_duplicate_insert() -> None:
    existing = Invocation(
        id=404,
        consumer_account_id=12,
        service_id=101,
        endpoint_id=303,
        endpoint_key="translate",
        access_mode=AccessMode.FREE,
        quote_id=None,
        idempotency_key="invoke-key",
        request_hash="a" * 64,
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "bonjour"},
        upstream_status_code=200,
        error_message=None,
    )
    session = FakeSession(raise_integrity_on_commit=True)
    service = InvokeService(cast("AsyncSession", session), http_client=FakeHttpClient())
    service._invocation_repo = FakeInvocationRepository(existing)

    invocation = await service.execute(
        ActorContext(account_id=12),
        resolved=_resolved_target(),
        idempotency_key="invoke-key",
    )

    assert invocation is existing
    assert session.commits == 1
    assert session.rollbacks == 1
    assert service._invocation_repo.lookup_count == 2
    assert service._invocation_repo.add_calls == 1


@pytest.mark.asyncio
async def test_execute_commits_before_and_after_upstream_call() -> None:
    session = FakeSuccessSession()
    http_client = FakeHttpClient()
    service = InvokeService(cast("AsyncSession", session), http_client=http_client)
    service._invocation_repo = FakeNewInvocationRepository()

    invocation = await service.execute(
        ActorContext(account_id=12),
        resolved=_resolved_target(),
        idempotency_key="invoke-key",
    )

    assert invocation.status is InvocationStatus.SUCCEEDED
    assert invocation.response_payload == {"result": "bonjour"}
    assert session.commits == 2
    assert session.flushes == 1
    assert session.refreshes == 2
    assert http_client.calls == 1


@pytest.mark.asyncio
async def test_get_replayable_invocation_uses_failure_reason_for_timeout_replays() -> None:
    existing = Invocation(
        id=606,
        consumer_account_id=12,
        service_id=101,
        endpoint_id=303,
        endpoint_key="translate",
        access_mode=AccessMode.FREE,
        quote_id=None,
        idempotency_key="invoke-key",
        request_hash="a" * 64,
        status=InvocationStatus.FAILED,
        response_payload=None,
        upstream_status_code=None,
        error_message="custom error text",
        failure_reason=InvocationFailureReason.UPSTREAM_TIMEOUT,
    )
    session = FakeSuccessSession()
    service = InvokeService(cast("AsyncSession", session), http_client=FakeHttpClient())
    service._invocation_repo = FakeInvocationRepository(existing)
    service._invocation_repo.lookup_count = 1

    with pytest.raises(InvokeGatewayTimeoutError, match="upstream request timed out"):
        await service.get_replayable_invocation(
            ActorContext(account_id=12),
            idempotency_key="invoke-key",
            request_hash="a" * 64,
        )


@pytest.mark.asyncio
async def test_execute_reuses_same_invocation_id_after_late_commit_failure() -> None:
    repo = FakePersistedInvocationRepository()
    session = FakeCommitSequenceSession(
        fail_on_commit_calls={2},
        on_successful_commit=repo.persist,
    )
    http_client = FakeIdempotentHttpClient()
    service = InvokeService(cast("AsyncSession", session), http_client=http_client)
    service._invocation_repo = repo

    with pytest.raises(RuntimeError, match="commit failed"):
        await service.execute(
            ActorContext(account_id=12),
            resolved=_resolved_target(),
            idempotency_key="invoke-key",
        )

    assert repo.stored_invocation is not None
    assert repo.stored_invocation.status is InvocationStatus.IN_PROGRESS

    invocation = await service.execute(
        ActorContext(account_id=12),
        resolved=_resolved_target(),
        idempotency_key="invoke-key",
    )

    assert invocation.status is InvocationStatus.SUCCEEDED
    assert repo.add_calls == 1
    assert session.commits == 3
    assert http_client.calls == ["505", "505"]
    assert http_client.side_effect_invocation_ids == {"505"}
