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
    def __init__(self) -> None:
        self.flushes = 0
        self.commits = 0
        self.refreshes = 0
        self.rollbacks = 0
        self.begin_nested_calls = 0

    async def flush(self) -> None:
        self.flushes += 1
        raise IntegrityError("statement", {}, Exception("duplicate invocation"))

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, instance: object) -> None:
        _ = instance
        self.refreshes += 1

    def begin_nested(self) -> "_FakeNestedTransaction":
        self.begin_nested_calls += 1
        return _FakeNestedTransaction()


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
        response_payload: dict[str, object] | None,
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
        self.begin_nested_calls = 0

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, instance: object) -> None:
        _ = instance
        self.refreshes += 1

    def begin_nested(self) -> "_FakeNestedTransaction":
        self.begin_nested_calls += 1
        return _FakeNestedTransaction()


class _FakeNestedTransaction:
    async def __aenter__(self) -> "_FakeNestedTransaction":
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> bool:
        _ = exc_type
        _ = exc
        _ = tb
        return False


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls = 0

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, object],
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
        response_payload: dict[str, object] | None,
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
        base_url="https://provider.internal",
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
async def test_execute_replays_existing_invocation_when_flush_hits_duplicate_insert() -> None:
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
    session = FakeSession()
    service = InvokeService(cast("AsyncSession", session), http_client=FakeHttpClient())
    service._invocation_repo = FakeInvocationRepository(existing)

    invocation = await service.execute(
        ActorContext(account_id=12),
        resolved=_resolved_target(),
        idempotency_key="invoke-key",
    )

    assert invocation is existing
    assert session.commits == 0
    assert session.rollbacks == 0
    assert session.begin_nested_calls == 1
    assert service._invocation_repo.lookup_count == 2
    assert service._invocation_repo.add_calls == 1


@pytest.mark.asyncio
async def test_execute_skips_commit_when_auto_commit_is_disabled() -> None:
    session = FakeSuccessSession()
    http_client = FakeHttpClient()
    service = InvokeService(cast("AsyncSession", session), http_client=http_client)
    service._invocation_repo = FakeNewInvocationRepository()

    invocation = await service.execute(
        ActorContext(account_id=12),
        resolved=_resolved_target(),
        idempotency_key="invoke-key",
        auto_commit=False,
    )

    assert invocation.status is InvocationStatus.SUCCEEDED
    assert invocation.response_payload == {"result": "bonjour"}
    assert session.commits == 0
    assert session.flushes == 2
    assert session.refreshes == 1
    assert session.begin_nested_calls == 1
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
