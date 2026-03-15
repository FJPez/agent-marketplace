from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.enums import InvocationStatus
from app.core.request_hash import hash_request_body
from app.integrations.provider_gateway.client import (
    ProviderGatewayClient,
    ProviderGatewayResponseError,
    ProviderGatewayTimeoutError,
    ProviderGatewayTransportError,
    SupportsRequest,
)
from app.integrations.provider_gateway.signing import HmacAuthConfig, get_hmac_auth_config
from app.repositories.consumer_profile_repo import ConsumerProfileRepository
from app.repositories.invocation_repo import InvocationRepository
from app.repositories.service_repo import ServiceRepository
from app.services.moderation_service import ModerationService, ServiceUnavailableError
from app.services.quote_service import (
    QuoteExpiredError,
    QuoteMismatchError,
    QuoteNotFoundError,
    QuoteService,
    QuoteStaleError,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.actor import ActorContext
    from app.db.models import Invocation, Quote, Service, ServiceEndpoint


class InvokeNotFoundError(Exception):
    pass


class InvokeConflictError(Exception):
    pass


class InvokeBadGatewayError(Exception):
    pass


class InvokeGatewayTimeoutError(Exception):
    pass


class InvokeForbiddenError(Exception):
    pass


class InvokeUnavailableError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedInvokeTarget:
    service: Service
    endpoint: ServiceEndpoint
    request_hash: str
    quote: Quote | None
    auth: HmacAuthConfig
    payload: dict[str, object]


class InvokeService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        http_client: SupportsRequest,
    ) -> None:
        self._session = session
        self._http_client = http_client
        self._consumer_profile_repo = ConsumerProfileRepository(session)
        self._service_repo = ServiceRepository(session)
        self._quote_service = QuoteService(session)
        self._moderation_service = ModerationService(session)
        self._invocation_repo = InvocationRepository(session)

    async def resolve_target(
        self,
        actor: ActorContext,
        *,
        service_id_or_slug: str,
        endpoint_key: str,
        payload: dict[str, object],
        quote_id: int | None,
    ) -> ResolvedInvokeTarget:
        await self._require_consumer_profile(actor.account_id)

        service = await self._service_repo.get_public(service_id_or_slug=service_id_or_slug)
        if service is None:
            raise InvokeNotFoundError("service not found")
        try:
            await self._moderation_service.ensure_service_listed(service.id)
        except ServiceUnavailableError as exc:
            raise InvokeNotFoundError("service not found") from exc

        endpoint = next(
            (item for item in service.endpoints if item.key == endpoint_key and item.is_enabled),
            None,
        )
        if endpoint is None:
            raise InvokeNotFoundError("endpoint not found")
        if endpoint.upstream is None:
            raise InvokeUnavailableError("service endpoint is not invokable")
        auth = get_hmac_auth_config(endpoint.upstream.config)
        if auth is None:
            raise InvokeUnavailableError("service endpoint is not invokable")

        request_hash = hash_request_body(
            {
                "service_id": service.id,
                "endpoint_key": endpoint_key,
                "payload": payload,
                "quote_id": quote_id,
            },
        )
        quote: Quote | None = None
        if quote_id is not None:
            try:
                quote = await self._quote_service.validate_quote(
                    quote_id=quote_id,
                    payload=payload,
                )
            except (
                QuoteNotFoundError,
                QuoteMismatchError,
                QuoteExpiredError,
                QuoteStaleError,
            ) as exc:
                raise InvokeConflictError("quote is not valid for invoke") from exc
            if quote.service_id != service.id or quote.endpoint_key != endpoint_key:
                raise InvokeConflictError("quote is not valid for invoke")

        return ResolvedInvokeTarget(
            service=service,
            endpoint=endpoint,
            request_hash=request_hash,
            quote=quote,
            auth=auth,
            payload=payload,
        )

    async def execute(
        self,
        actor: ActorContext,
        *,
        resolved: ResolvedInvokeTarget,
        idempotency_key: str,
    ) -> Invocation:
        existing = await self._invocation_repo.get_by_idempotency_key(
            consumer_account_id=actor.account_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_hash != resolved.request_hash:
                raise InvokeConflictError("idempotency key already used for a different request")
            return existing

        invocation = self._invocation_repo.add(
            consumer_account_id=actor.account_id,
            service_id=resolved.service.id,
            endpoint_id=resolved.endpoint.id,
            endpoint_key=resolved.endpoint.key,
            access_mode=resolved.endpoint.access_mode,
            quote_id=None if resolved.quote is None else resolved.quote.id,
            idempotency_key=idempotency_key,
            request_hash=resolved.request_hash,
            status=InvocationStatus.FAILED,
            response_payload=None,
            upstream_status_code=None,
            error_message=None,
        )
        await self._session.flush()

        gateway_client = ProviderGatewayClient(self._http_client)
        assert resolved.endpoint.upstream is not None
        try:
            gateway_result = await gateway_client.invoke(
                base_url=resolved.endpoint.upstream.base_url,
                path=resolved.endpoint.upstream.path,
                http_method=resolved.endpoint.upstream.http_method,
                payload=resolved.payload,
                request_hash=resolved.request_hash,
                invocation_id=invocation.id,
                timeout_seconds=resolved.endpoint.timeout_seconds,
                auth=resolved.auth,
            )
        except ProviderGatewayTimeoutError as exc:
            invocation.error_message = "upstream request timed out"
            await self._commit_and_refresh(invocation)
            raise InvokeGatewayTimeoutError("upstream request timed out") from exc
        except ProviderGatewayTransportError as exc:
            invocation.error_message = "upstream request failed"
            await self._commit_and_refresh(invocation)
            raise InvokeBadGatewayError("upstream request failed") from exc
        except ProviderGatewayResponseError as exc:
            invocation.error_message = str(exc)
            invocation.upstream_status_code = exc.upstream_status_code
            await self._commit_and_refresh(invocation)
            raise InvokeBadGatewayError(str(exc)) from exc

        invocation.status = InvocationStatus.SUCCEEDED
        invocation.response_payload = gateway_result.payload
        invocation.upstream_status_code = gateway_result.status_code
        invocation.error_message = None
        await self._commit_and_refresh(invocation)
        return invocation

    async def get_invocation(
        self,
        actor: ActorContext,
        *,
        invocation_id: int,
    ) -> Invocation:
        await self._require_consumer_profile(actor.account_id)
        invocation = await self._invocation_repo.get_for_consumer(
            invocation_id=invocation_id,
            consumer_account_id=actor.account_id,
        )
        if invocation is None:
            raise InvokeNotFoundError("invocation not found")
        return invocation

    async def list_invocations(self, actor: ActorContext) -> list[Invocation]:
        await self._require_consumer_profile(actor.account_id)
        return await self._invocation_repo.list_for_consumer(consumer_account_id=actor.account_id)

    async def _require_consumer_profile(self, account_id: int) -> None:
        profile = await self._consumer_profile_repo.get_by_account_id(account_id)
        if profile is None:
            raise InvokeNotFoundError("consumer profile not found")

    async def _commit_and_refresh(self, invocation: Invocation) -> None:
        await self._session.commit()
        await self._session.refresh(invocation)
