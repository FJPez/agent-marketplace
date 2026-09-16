"""Invoke target resolution, replay, and durable single-forward execution."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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


@dataclass(frozen=True, slots=True)
class ResolvedInvokeTarget:
    service: Service
    endpoint: ServiceEndpoint
    request_hash: str
    quote: Quote | None
    auth: HmacAuthConfig
    payload: object


async def resolve_target(
    *,
    session: AsyncSession,
    service_ref: PublicServiceRef,
    endpoint_key: str,
    payload: object,
    quote_id: int | None,
) -> ResolvedInvokeTarget:
    """Resolve an invokable endpoint and bind the request to its optional quote."""
    statement = select(Service).where(
        Service.lifecycle == ServiceLifecycle.ACTIVE,
        Service.id == service_ref if isinstance(service_ref, int) else Service.slug == service_ref,
    )
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
    return _replay_stored_invocation(existing, now=datetime.now(UTC))


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
            if existing.request_hash != resolved.request_hash:
                raise ConflictError("idempotency key already used for a different request")
            replayed = _replay_stored_invocation(existing, now=datetime.now(UTC))
        except (NotFoundError, ConflictError, UpstreamError, UpstreamTimeoutError):
            await session.rollback()
            raise
        await session.commit()
        return replayed

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
            invocation_id=claimed.id,
            timeout_seconds=resolved.endpoint.timeout_seconds,
            auth=resolved.auth,
        )
    except (
        ProviderGatewayTargetError,
        ProviderGatewayTimeoutError,
        ProviderGatewayTransportError,
        ProviderGatewayResponseError,
    ) as exc:
        if isinstance(exc, ProviderGatewayTimeoutError):
            failure_reason = InvocationFailureReason.UPSTREAM_TIMEOUT
            message = "upstream request timed out"
            failed_status_code: int | None = None
            error: UpstreamError | UpstreamTimeoutError = UpstreamTimeoutError(message)
        elif isinstance(exc, ProviderGatewayResponseError):
            failure_reason = InvocationFailureReason.UPSTREAM_RESPONSE
            message = str(exc)
            failed_status_code = exc.upstream_status_code
            error = UpstreamError(message)
        else:
            # An unsafe target and a transport error both mean the upstream never answered.
            failure_reason = InvocationFailureReason.UPSTREAM_TRANSPORT
            message = str(exc)
            failed_status_code = None
            error = UpstreamError(message)

        claimed.status = InvocationStatus.FAILED
        claimed.failure_reason = failure_reason
        claimed.error_message = message
        claimed.upstream_status_code = failed_status_code
        claimed.in_progress_until = None
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
        raise error from exc

    claimed.status = InvocationStatus.SUCCEEDED
    claimed.response_payload = to_json_value(gateway_result.payload)
    claimed.upstream_status_code = gateway_result.status_code
    claimed.error_message = None
    claimed.failure_reason = None
    claimed.in_progress_until = None
    await session.commit()
    # The outcome is durable before it is announced, so no log claims a success the
    # database never kept.
    logger.info(
        "invoke succeeded",
        extra=build_event_context(
            "invoke.succeeded",
            **{
                ACCOUNT_ID_FIELD: account_id,
                INVOCATION_ID_FIELD: claimed.id,
                SERVICE_ID_FIELD: resolved.service.id,
            },
        ),
    )
    return claimed


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


def _replay_stored_invocation(invocation: Invocation, *, now: datetime) -> Invocation:
    """Answer a repeated request from the invocation row alone, never forwarding it again."""
    if invocation.status is InvocationStatus.SUCCEEDED:
        return invocation
    if invocation.status is InvocationStatus.FAILED:
        if invocation.failure_reason is InvocationFailureReason.UPSTREAM_TIMEOUT:
            raise UpstreamTimeoutError("upstream request timed out")
        raise UpstreamError(invocation.error_message or "upstream request failed")
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
