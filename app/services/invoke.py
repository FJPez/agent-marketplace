"""Invoke target resolution, replay, and durable single-forward execution."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.enums import (
    InvocationFailureReason,
    InvocationStatus,
    ServiceLifecycle,
)
from app.core.errors import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    UpstreamError,
    UpstreamTimeoutError,
)
from app.core.json_types import to_json_value
from app.core.logging import (
    ACCOUNT_ID_FIELD,
    INVOCATION_ID_FIELD,
    SERVICE_ID_FIELD,
    build_event_context,
    get_logger,
)
from app.core.request_hash import hash_request_body
from app.core.request_schema_validation import PayloadSchemaMismatchError, validate_request_payload
from app.db.models import Invocation, Quote, Service, ServiceEndpoint
from app.integrations.provider_gateway.client import (
    ProviderGatewayClient,
    ProviderGatewayResponseError,
    ProviderGatewayTargetError,
    ProviderGatewayTimeoutError,
    ProviderGatewayTransportError,
    SupportsRequest,
)
from app.integrations.provider_gateway.signing import HmacAuthConfig, get_hmac_auth_config
from app.schemas.service_ref import PublicServiceRef
from app.services import moderation, quotes
from app.services.moderation import ServiceUnavailableError
from app.services.quotes import QuoteExpiredError, QuoteMismatchError, QuoteStaleError

logger = get_logger(__name__)

# Slack on top of the endpoint timeout so a worker still waiting on a slow upstream
# is never mistaken for one that died.
LEASE_GRACE_SECONDS = 30

GatewayFailureError = (
    ProviderGatewayTargetError
    | ProviderGatewayTimeoutError
    | ProviderGatewayTransportError
    | ProviderGatewayResponseError
)


@dataclass(frozen=True, slots=True)
class ResolvedInvokeTarget:
    service: Service
    endpoint: ServiceEndpoint
    request_hash: str
    quote: Quote | None
    auth: HmacAuthConfig
    payload: object


@dataclass(frozen=True, slots=True)
class GatewayFailure:
    failure_reason: InvocationFailureReason
    message: str
    upstream_status_code: int | None
    error: UpstreamError | UpstreamTimeoutError


async def resolve_target(
    *,
    session: AsyncSession,
    service_ref: PublicServiceRef,
    endpoint_key: str,
    payload: object,
    quote_id: int | None,
) -> ResolvedInvokeTarget:
    """Resolve an invokable endpoint and bind the request to its optional quote."""
    statement = select(Service).where(Service.lifecycle == ServiceLifecycle.ACTIVE)
    if isinstance(service_ref, int):
        statement = statement.where(Service.id == service_ref)
    else:
        statement = statement.where(Service.slug == service_ref)
    service = await session.scalar(statement)
    if service is None:
        raise NotFoundError("service not found")

    try:
        await moderation.ensure_service_available(session=session, service_id=service.id)
    except ServiceUnavailableError as exc:
        # Suspended and delisted services must stay indistinguishable from missing ones publicly.
        raise NotFoundError("service not found") from exc

    endpoint = await session.scalar(
        select(ServiceEndpoint)
        # One endpoint, one-to-one upstream: a join beats a second SELECT here.
        .options(joinedload(ServiceEndpoint.upstream))
        .where(
            ServiceEndpoint.service_id == service.id,
            ServiceEndpoint.key == endpoint_key,
            ServiceEndpoint.is_enabled.is_(True),
        ),
    )
    # A disabled endpoint is indistinguishable from a missing one for invoking.
    if endpoint is None:
        raise NotFoundError("endpoint not found")

    upstream = endpoint.upstream
    if upstream is None:
        raise InvalidStateError("service endpoint is not invokable")
    auth = get_hmac_auth_config(upstream.config)
    if auth is None:
        raise InvalidStateError("service endpoint is not invokable")

    try:
        validate_request_payload(payload=payload, request_schema=endpoint.request_schema)
    except PayloadSchemaMismatchError as exc:
        # Invoke answers a schema mismatch with 409, not the 422 the shared validator maps to.
        raise ConflictError(str(exc)) from exc

    request_hash = _build_request_hash(
        service_id=service.id,
        endpoint_key=endpoint_key,
        payload=payload,
        quote_id=quote_id,
    )
    quote: Quote | None = None
    if quote_id is not None:
        try:
            quote = await quotes.validate_quote(
                session=session,
                quote_id=quote_id,
                payload=payload,
            )
        except (
            NotFoundError,
            QuoteMismatchError,
            QuoteExpiredError,
            QuoteStaleError,
        ) as exc:
            raise ConflictError("quote is not valid for invoke") from exc
        if quote.service_id != service.id or quote.endpoint_key != endpoint_key:
            raise ConflictError("quote is not valid for invoke")

    return ResolvedInvokeTarget(
        service=service,
        endpoint=endpoint,
        request_hash=request_hash,
        quote=quote,
        auth=auth,
        payload=payload,
    )


async def try_replay(
    *,
    session: AsyncSession,
    account_id: int,
    service_ref: PublicServiceRef,
    endpoint_key: str,
    payload: object,
    quote_id: int | None,
    idempotency_key: str,
) -> Invocation | None:
    """Interpret the stored outcome of a repeated request, ahead of any current-state checks."""
    existing = await session.scalar(
        select(Invocation).where(
            Invocation.consumer_account_id == account_id,
            Invocation.idempotency_key == idempotency_key,
        ),
    )
    if existing is None:
        return None

    if isinstance(service_ref, int):
        if existing.service_id != service_ref:
            raise ConflictError("idempotency key already used for a different request")
    else:
        stored_slug = await session.scalar(
            select(Service.slug).where(Service.id == existing.service_id),
        )
        if stored_slug is None:
            return None
        if stored_slug != service_ref:
            raise ConflictError("idempotency key already used for a different request")

    if existing.request_hash != _build_request_hash(
        service_id=existing.service_id,
        endpoint_key=endpoint_key,
        payload=payload,
        quote_id=quote_id,
    ):
        raise ConflictError("idempotency key already used for a different request")

    # The caller and the request are the same, so what the row already says about this
    # invocation settles it. Current service, endpoint, and quote state describes the next
    # execution, not this one, and must not mask a stored outcome.
    return interpret_stored_outcome(existing, now=datetime.now(UTC))


async def execute(
    *,
    session: AsyncSession,
    account_id: int,
    resolved: ResolvedInvokeTarget,
    idempotency_key: str,
    http_client: SupportsRequest,
) -> Invocation:
    """Claim the invocation, forward it upstream at most once, and store the outcome."""
    claimed = await _claim_invocation(
        session=session,
        account_id=account_id,
        resolved=resolved,
        idempotency_key=idempotency_key,
    )
    # Ownership becomes durable here, before the provider is contacted, and the forward
    # below then runs with no transaction open.
    await session.commit()

    if claimed is None:
        try:
            existing = await _resolve_claimed_invocation(
                session=session,
                account_id=account_id,
                idempotency_key=idempotency_key,
                request_hash=resolved.request_hash,
            )
        except (NotFoundError, ConflictError, UpstreamError, UpstreamTimeoutError):
            await session.rollback()
            raise
        await session.commit()
        return existing

    try:
        invocation = await _forward_and_record(
            resolved=resolved,
            invocation=claimed,
            http_client=http_client,
        )
    except (UpstreamError, UpstreamTimeoutError):
        # The terminal failure the helper recorded is durable even though the call raises.
        await session.commit()
        logger.error(
            "invoke failed",
            extra=build_event_context(
                "invoke.failed",
                **{
                    ACCOUNT_ID_FIELD: account_id,
                    INVOCATION_ID_FIELD: claimed.id,
                    SERVICE_ID_FIELD: resolved.service.id,
                },
            ),
        )
        raise
    await session.commit()
    # The outcome is durable before it is announced, so no log claims a success the
    # database never kept.
    logger.info(
        "invoke succeeded",
        extra=build_event_context(
            "invoke.succeeded",
            **{
                ACCOUNT_ID_FIELD: account_id,
                INVOCATION_ID_FIELD: invocation.id,
                SERVICE_ID_FIELD: resolved.service.id,
            },
        ),
    )
    return invocation


async def get_invocation(
    *,
    session: AsyncSession,
    account_id: int,
    invocation_id: int,
) -> Invocation:
    """Return the single invocation the account owns."""
    invocation = await session.scalar(
        select(Invocation).where(
            Invocation.id == invocation_id,
            Invocation.consumer_account_id == account_id,
        ),
    )
    if invocation is None:
        raise NotFoundError("invocation not found")
    return invocation


async def list_invocations(*, session: AsyncSession, account_id: int) -> list[Invocation]:
    """Return the account's invocations, newest first."""
    result = await session.scalars(
        select(Invocation)
        .where(Invocation.consumer_account_id == account_id)
        .order_by(desc(Invocation.created_at), desc(Invocation.id)),
    )
    return list(result.all())


def interpret_stored_outcome(invocation: Invocation, *, now: datetime) -> Invocation:
    """Answer a repeated request from the invocation row alone, never forwarding it again."""
    if invocation.status is InvocationStatus.SUCCEEDED:
        return invocation
    if invocation.status is InvocationStatus.FAILED:
        _raise_stored_failure(invocation)
    if invocation.in_progress_until is not None and invocation.in_progress_until > now:
        raise ConflictError("request already in progress")
    # A missing or expired lease says only that the claiming worker stopped reporting.
    # The upstream may or may not have run, so nothing is forwarded again.
    raise ConflictError("invocation outcome is unknown; recovery required")


async def _claim_invocation(
    *,
    session: AsyncSession,
    account_id: int,
    resolved: ResolvedInvokeTarget,
    idempotency_key: str,
) -> Invocation | None:
    """Insert the in-progress row with its lease, or return None when a claim already exists.

    The caller owns the transaction and commits the claim.
    """
    now = datetime.now(UTC)
    # Ownership is this insert: the row and its lease become durable together, so no
    # unclaimed in-progress row is ever visible and the unique index, not a prior read,
    # decides which caller owns the forward. Conflicts are skipped rather than raised so
    # the shared session never has to roll back, which would expire every loaded object.
    claim = (
        insert(Invocation)
        .values(
            consumer_account_id=account_id,
            service_id=resolved.service.id,
            endpoint_id=resolved.endpoint.id,
            endpoint_key=resolved.endpoint.key,
            access_mode=resolved.endpoint.access_mode,
            quote_id=None if resolved.quote is None else resolved.quote.id,
            idempotency_key=idempotency_key,
            request_hash=resolved.request_hash,
            status=InvocationStatus.IN_PROGRESS,
            in_progress_until=now
            + timedelta(seconds=resolved.endpoint.timeout_seconds + LEASE_GRACE_SECONDS),
            response_payload=None,
            upstream_status_code=None,
            error_message=None,
            failure_reason=None,
        )
        .on_conflict_do_nothing(index_elements=["consumer_account_id", "idempotency_key"])
        # RETURNING the mapped entity hands back the persistent instance itself, so the
        # forward can mutate the claimed row without a second read.
        .returning(Invocation)
    )
    return await session.scalar(claim)


async def _resolve_claimed_invocation(
    *,
    session: AsyncSession,
    account_id: int,
    idempotency_key: str,
    request_hash: str,
) -> Invocation:
    """Replay or reject a request whose invocation another attempt already claimed.

    The caller owns the transaction and ends the locked read.
    """
    existing = await session.scalar(
        select(Invocation)
        .where(
            Invocation.consumer_account_id == account_id,
            Invocation.idempotency_key == idempotency_key,
        )
        # The row may already sit in this session from the replay lookup, and the
        # locked read has to see what the other worker committed since then.
        .execution_options(populate_existing=True)
        .with_for_update(),
    )
    if existing is None:
        raise NotFoundError("invocation not found")
    if existing.request_hash != request_hash:
        raise ConflictError("idempotency key already used for a different request")
    return interpret_stored_outcome(existing, now=datetime.now(UTC))


async def _forward_and_record(
    *,
    resolved: ResolvedInvokeTarget,
    invocation: Invocation,
    http_client: SupportsRequest,
) -> Invocation:
    """Call the provider and apply the terminal state to the supplied invocation.

    The caller persists that state, success or failure.
    """
    upstream = resolved.endpoint.upstream
    if upstream is None:
        raise InvalidStateError("service endpoint is not invokable")

    gateway_client = ProviderGatewayClient(http_client)
    try:
        gateway_result = await gateway_client.invoke(
            base_url=upstream.base_url,
            path=upstream.path,
            http_method=upstream.http_method,
            payload=resolved.payload,
            request_hash=resolved.request_hash,
            invocation_id=invocation.id,
            timeout_seconds=resolved.endpoint.timeout_seconds,
            auth=resolved.auth,
        )
    except (
        ProviderGatewayTargetError,
        ProviderGatewayTimeoutError,
        ProviderGatewayTransportError,
        ProviderGatewayResponseError,
    ) as exc:
        failure = _classify_gateway_failure(exc)
        invocation.status = InvocationStatus.FAILED
        invocation.failure_reason = failure.failure_reason
        invocation.error_message = failure.message
        invocation.upstream_status_code = failure.upstream_status_code
        invocation.in_progress_until = None
        raise failure.error from exc

    invocation.status = InvocationStatus.SUCCEEDED
    invocation.response_payload = to_json_value(gateway_result.payload)
    invocation.upstream_status_code = gateway_result.status_code
    invocation.error_message = None
    invocation.failure_reason = None
    invocation.in_progress_until = None
    return invocation


def _classify_gateway_failure(exc: GatewayFailureError) -> GatewayFailure:
    if isinstance(exc, ProviderGatewayTimeoutError):
        return GatewayFailure(
            failure_reason=InvocationFailureReason.UPSTREAM_TIMEOUT,
            message="upstream request timed out",
            upstream_status_code=None,
            error=UpstreamTimeoutError("upstream request timed out"),
        )
    if isinstance(exc, ProviderGatewayResponseError):
        return GatewayFailure(
            failure_reason=InvocationFailureReason.UPSTREAM_RESPONSE,
            message=str(exc),
            upstream_status_code=exc.upstream_status_code,
            error=UpstreamError(str(exc)),
        )
    # An unsafe target and a transport error both mean the upstream never answered.
    return GatewayFailure(
        failure_reason=InvocationFailureReason.UPSTREAM_TRANSPORT,
        message=str(exc),
        upstream_status_code=None,
        error=UpstreamError(str(exc)),
    )


def _raise_stored_failure(invocation: Invocation) -> NoReturn:
    if invocation.failure_reason is InvocationFailureReason.UPSTREAM_TIMEOUT:
        raise UpstreamTimeoutError("upstream request timed out")
    raise UpstreamError(invocation.error_message or "upstream request failed")


def _build_request_hash(
    *,
    service_id: int,
    endpoint_key: str,
    payload: object,
    quote_id: int | None,
) -> str:
    return hash_request_body(
        {
            "service_id": service_id,
            "endpoint_key": endpoint_key,
            "payload": payload,
            "quote_id": quote_id,
        },
    )
