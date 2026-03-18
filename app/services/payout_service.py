from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.core.enums import PayoutFailureCode, PayoutStatus
from app.core.logging import (
    INVOCATION_ID_FIELD,
    PAYMENT_ATTEMPT_ID_FIELD,
    PAYOUT_COUNT_FIELD,
    PAYOUT_ID_FIELD,
    PAYOUT_STATUS_FIELD,
    PROVIDER_ACCOUNT_ID_FIELD,
    SERVICE_ID_FIELD,
    TRANSFER_REFERENCE_FIELD,
    build_event_context,
    get_logger,
)
from app.integrations.payouts import PayoutExecutionError, PreparedPayout, SupportsPayoutExecutor
from app.repositories.account_repo import AccountRepository
from app.repositories.payout_repo import (
    PayoutExecutionRepository,
    PayoutReportingRepository,
    PayoutSummary,
)
from app.services.ledger_service import split_paid_invocation_amount

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.actor import ActorContext
    from app.db.models import Account, Payout

logger = get_logger(__name__)


class PayoutConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PayoutRequestResult:
    idempotency_key: str
    payouts: list[Payout]

    @property
    def requested_count(self) -> int:
        return len(self.payouts)

    @property
    def sent_count(self) -> int:
        return sum(1 for payout in self.payouts if payout.status is PayoutStatus.SENT)

    @property
    def failed_count(self) -> int:
        return sum(1 for payout in self.payouts if payout.status is PayoutStatus.FAILED)


class PayoutReportingStore(Protocol):
    async def list_for_provider(
        self,
        *,
        provider_account_id: int,
        status: PayoutStatus | None = None,
    ) -> list[Payout]: ...

    async def summarize_for_provider(
        self,
        *,
        provider_account_id: int,
        status: PayoutStatus | None = None,
    ) -> list[PayoutSummary]: ...


class PayoutExecutionStore(Protocol):
    def add(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        destination_wallet: str | None,
        amount_minor: int,
        currency: str,
        network: str,
        status: PayoutStatus,
        transfer_reference: str | None = None,
        request_idempotency_key: str | None = None,
        failure_code: PayoutFailureCode | None = None,
        error_message: str | None = None,
        attempt_count: int = 1,
        prepared_raw_transaction: str | None = None,
        chain_nonce: int | None = None,
    ) -> Payout: ...

    async def get_by_payment_attempt_id(self, *, payment_attempt_id: int) -> Payout | None: ...

    async def claim_treasury_lock(self) -> None: ...

    async def list_for_provider_request(
        self,
        *,
        provider_account_id: int,
        request_idempotency_key: str,
        for_update: bool = False,
    ) -> list[Payout]: ...

    async def list_in_flight_for_provider(
        self,
        *,
        provider_account_id: int,
    ) -> list[Payout]: ...

    async def claim_ready_for_provider(
        self,
        *,
        provider_account_id: int,
    ) -> list[Payout]: ...

    async def get_max_claimed_chain_nonce(self) -> int | None: ...


class AccountStore(Protocol):
    async def get(self, account_id: int) -> Account | None: ...


class PayoutReportingService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        payout_repo: PayoutReportingStore | None = None,
    ) -> None:
        self._payout_repo = payout_repo or PayoutReportingRepository(session)

    async def get_provider_payouts(
        self,
        actor: ActorContext,
        *,
        status: PayoutStatus | None = None,
    ) -> list[Payout]:
        payouts = await self._payout_repo.list_for_provider(
            provider_account_id=actor.account_id,
            status=status,
        )
        logger.info(
            "provider payouts listed",
            extra=build_event_context(
                "payout.reporting_listed",
                **{
                    PROVIDER_ACCOUNT_ID_FIELD: actor.account_id,
                    PAYOUT_STATUS_FIELD: None if status is None else status.value,
                    PAYOUT_COUNT_FIELD: len(payouts),
                },
            ),
        )
        return payouts

    async def get_provider_payout_summaries(
        self,
        actor: ActorContext,
        *,
        status: PayoutStatus | None = None,
    ) -> list[PayoutSummary]:
        return await self._payout_repo.summarize_for_provider(
            provider_account_id=actor.account_id,
            status=status,
        )


class PayoutExecutionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        payout_repo: PayoutExecutionStore | None = None,
        account_repo: AccountStore | None = None,
        payout_executor: SupportsPayoutExecutor | None = None,
    ) -> None:
        self._session = session
        self._payout_repo = payout_repo or PayoutExecutionRepository(session)
        self._account_repo = account_repo or AccountRepository(session)
        self._payout_executor = payout_executor

    async def record_ready_payout(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        gross_amount_minor: int,
        currency: str,
        network: str,
    ) -> Payout:
        existing = await self._payout_repo.get_by_payment_attempt_id(
            payment_attempt_id=payment_attempt_id,
        )
        if existing is not None:
            return existing
        _, provider_amount_minor = split_paid_invocation_amount(gross_amount_minor)
        payout = self._payout_repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            destination_wallet=None,
            amount_minor=provider_amount_minor,
            currency=currency,
            network=network,
            status=PayoutStatus.READY,
            attempt_count=0,
        )
        if provider_amount_minor <= 0:
            payout.status = PayoutStatus.FAILED
            payout.failure_code = PayoutFailureCode.INVALID_AMOUNT
            payout.error_message = "provider payout amount must be positive"
        await self._session.flush()
        logger.info(
            "provider payout created",
            extra=build_event_context(
                "payout.ready",
                **{
                    PAYMENT_ATTEMPT_ID_FIELD: payment_attempt_id,
                    PAYOUT_ID_FIELD: payout.id,
                    PAYOUT_STATUS_FIELD: payout.status.value,
                    PROVIDER_ACCOUNT_ID_FIELD: provider_account_id,
                    INVOCATION_ID_FIELD: invocation_id,
                    SERVICE_ID_FIELD: service_id,
                },
            ),
        )
        return payout

    async def request_provider_payouts(
        self,
        actor: ActorContext,
        *,
        idempotency_key: str,
    ) -> PayoutRequestResult:
        if self._payout_executor is None:
            raise PayoutConflictError("payout executor is not configured")

        provider_wallet = await self._get_provider_wallet(provider_account_id=actor.account_id)
        if provider_wallet is None:
            raise PayoutConflictError("provider wallet address is not configured")

        await self._payout_repo.claim_treasury_lock()
        existing_batch = await self._payout_repo.list_for_provider_request(
            provider_account_id=actor.account_id,
            request_idempotency_key=idempotency_key,
            for_update=True,
        )
        if existing_batch:
            batch_count = len(existing_batch)
            is_terminal = self._batch_is_terminal(existing_batch)
            await self._session.rollback()
            if is_terminal:
                replay_batch = await self._payout_repo.list_for_provider_request(
                    provider_account_id=actor.account_id,
                    request_idempotency_key=idempotency_key,
                )
                logger.info(
                    "provider payout request replayed",
                    extra=build_event_context(
                        "payout.request_replayed",
                        **{
                            PROVIDER_ACCOUNT_ID_FIELD: actor.account_id,
                            PAYOUT_COUNT_FIELD: batch_count,
                        },
                    ),
                )
                return PayoutRequestResult(
                    idempotency_key=idempotency_key,
                    payouts=_order_payouts_for_response(replay_batch),
                )

            logger.info(
                "provider payout request already in progress",
                extra=build_event_context(
                    "payout.request_in_progress",
                    **{
                        PROVIDER_ACCOUNT_ID_FIELD: actor.account_id,
                        PAYOUT_COUNT_FIELD: batch_count,
                    },
                ),
            )
            raise PayoutConflictError("provider payout batch already in progress")

        in_flight = await self._payout_repo.list_in_flight_for_provider(
            provider_account_id=actor.account_id,
        )
        if in_flight:
            await self._session.rollback()
            raise PayoutConflictError("provider payout batch already in progress")

        ready_batch = await self._payout_repo.claim_ready_for_provider(
            provider_account_id=actor.account_id,
        )
        if not ready_batch:
            await self._session.rollback()
            raise PayoutConflictError("no ready payouts available")

        try:
            await self._prepare_batch(
                ready_batch,
                destination_wallet=provider_wallet,
                idempotency_key=idempotency_key,
            )
        except (PayoutExecutionError, RuntimeError, ValueError) as exc:
            await self._session.rollback()
            logger.error(
                "provider payout preparation failed",
                extra=build_event_context(
                    "payout.prepare_failed",
                    **{
                        PROVIDER_ACCOUNT_ID_FIELD: actor.account_id,
                        PAYOUT_COUNT_FIELD: len(ready_batch),
                    },
                ),
            )
            raise PayoutConflictError("payout could not be prepared") from exc

        await self._session.flush()
        await self._session.commit()
        logger.info(
            "provider payout request claimed",
            extra=build_event_context(
                "payout.request_claimed",
                **{
                    PROVIDER_ACCOUNT_ID_FIELD: actor.account_id,
                    PAYOUT_COUNT_FIELD: len(ready_batch),
                },
            ),
        )
        await self._send_batch(ready_batch)
        final_batch = await self._payout_repo.list_for_provider_request(
            provider_account_id=actor.account_id,
            request_idempotency_key=idempotency_key,
        )
        return PayoutRequestResult(
            idempotency_key=idempotency_key,
            payouts=_order_payouts_for_response(final_batch),
        )

    async def _prepare_batch(
        self,
        payouts: list[Payout],
        *,
        destination_wallet: str,
        idempotency_key: str,
    ) -> None:
        assert self._payout_executor is not None

        current_nonce = await self._payout_executor.current_nonce()
        max_chain_nonce = await self._payout_repo.get_max_claimed_chain_nonce()
        next_nonce = (
            current_nonce if max_chain_nonce is None else max(current_nonce, max_chain_nonce + 1)
        )
        for payout in payouts:
            prepared = await self._payout_executor.prepare_payout(
                destination_wallet=destination_wallet,
                amount_minor=payout.amount_minor,
                idempotency_key=idempotency_key,
                nonce=next_nonce,
            )
            self._apply_prepared_payout(
                payout,
                destination_wallet=destination_wallet,
                idempotency_key=idempotency_key,
                nonce=next_nonce,
                prepared=prepared,
            )
            next_nonce += 1

    async def _send_batch(self, payouts: list[Payout]) -> None:
        assert self._payout_executor is not None

        for payout in payouts:
            try:
                outcome = await self._payout_executor.send_prepared_payout(
                    raw_transaction=payout.prepared_raw_transaction or "",
                    reference=payout.transfer_reference or "",
                )
            except (PayoutExecutionError, RuntimeError, ValueError) as exc:
                payout.status = PayoutStatus.PENDING
                payout.failure_code = PayoutFailureCode.EXECUTOR_ERROR
                payout.error_message = str(exc)
                logger.error(
                    "provider payout remains pending",
                    extra=build_event_context(
                        "payout.failed",
                        **{
                            PAYOUT_ID_FIELD: payout.id,
                            PAYOUT_STATUS_FIELD: payout.status.value,
                            PROVIDER_ACCOUNT_ID_FIELD: payout.provider_account_id,
                            INVOCATION_ID_FIELD: payout.invocation_id,
                            SERVICE_ID_FIELD: payout.service_id,
                        },
                    ),
                )
                await self._session.commit()
                continue

            payout.status = PayoutStatus.SENT
            payout.transfer_reference = outcome.reference
            payout.failure_code = None
            payout.error_message = None
            logger.info(
                "provider payout sent",
                extra=build_event_context(
                    "payout.sent",
                    **{
                        PAYMENT_ATTEMPT_ID_FIELD: payout.payment_attempt_id,
                        PAYOUT_ID_FIELD: payout.id,
                        PAYOUT_STATUS_FIELD: payout.status.value,
                        PROVIDER_ACCOUNT_ID_FIELD: payout.provider_account_id,
                        INVOCATION_ID_FIELD: payout.invocation_id,
                        SERVICE_ID_FIELD: payout.service_id,
                        TRANSFER_REFERENCE_FIELD: payout.transfer_reference,
                    },
                ),
            )
            await self._session.commit()

    async def _get_provider_wallet(self, *, provider_account_id: int) -> str | None:
        account = await self._account_repo.get(provider_account_id)
        if account is None or account.wallet_address is None:
            return None
        normalized_wallet = account.wallet_address.strip()
        return normalized_wallet or None

    def _apply_prepared_payout(
        self,
        payout: Payout,
        *,
        destination_wallet: str,
        idempotency_key: str,
        nonce: int,
        prepared: PreparedPayout,
    ) -> None:
        payout.destination_wallet = destination_wallet
        payout.request_idempotency_key = idempotency_key
        payout.chain_nonce = nonce
        payout.prepared_raw_transaction = prepared.raw_transaction
        payout.transfer_reference = prepared.reference
        payout.status = PayoutStatus.PENDING
        payout.attempt_count += 1
        payout.failure_code = None
        payout.error_message = None

    def _batch_is_terminal(self, payouts: list[Payout]) -> bool:
        return all(payout.status in {PayoutStatus.SENT, PayoutStatus.FAILED} for payout in payouts)


def _order_payouts_for_response(payouts: list[Payout]) -> list[Payout]:
    return sorted(payouts, key=lambda payout: payout.id, reverse=True)
