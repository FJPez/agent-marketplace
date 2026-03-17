from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import text

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
from app.integrations.payouts import PayoutExecutionError, SupportsPayoutExecutor
from app.repositories.account_repo import AccountRepository
from app.repositories.payout_repo import PayoutRepository, PayoutSummary
from app.services.ledger_service import split_paid_invocation_amount

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.actor import ActorContext
    from app.db.models import Account, Payout

logger = get_logger(__name__)
_PAYOUT_TREASURY_LOCK_KEY = 84_532_001


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


class PayoutStore(Protocol):
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

    async def list_for_provider(
        self,
        *,
        provider_account_id: int,
        status: PayoutStatus | None = None,
    ) -> list[Payout]: ...

    async def list_for_provider_request(
        self,
        *,
        provider_account_id: int,
        request_idempotency_key: str,
    ) -> list[Payout]: ...

    async def list_ready_for_provider_for_update(
        self, *, provider_account_id: int
    ) -> list[Payout]: ...

    async def list_pending(self) -> list[Payout]: ...

    async def get_max_chain_nonce(self) -> int | None: ...

    async def summarize_for_provider(
        self,
        *,
        provider_account_id: int,
    ) -> list[PayoutSummary]: ...


class AccountStore(Protocol):
    async def get(self, account_id: int) -> Account | None: ...


class PayoutReportingService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        payout_repo: PayoutStore | None = None,
    ) -> None:
        self._session = session
        self._payout_repo = payout_repo or PayoutRepository(session)

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

    async def get_provider_payout_summaries(self, actor: ActorContext) -> list[PayoutSummary]:
        return await self._payout_repo.summarize_for_provider(provider_account_id=actor.account_id)


class PayoutExecutionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        payout_repo: PayoutStore | None = None,
        account_repo: AccountStore | None = None,
        payout_executor: SupportsPayoutExecutor | None = None,
    ) -> None:
        self._session = session
        self._payout_repo = payout_repo or PayoutRepository(session)
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
        replay = await self._payout_repo.list_for_provider_request(
            provider_account_id=actor.account_id,
            request_idempotency_key=idempotency_key,
        )
        if replay:
            logger.info(
                "provider payout request replayed",
                extra=build_event_context(
                    "payout.request_replayed",
                    **{
                        PROVIDER_ACCOUNT_ID_FIELD: actor.account_id,
                        PAYOUT_COUNT_FIELD: len(replay),
                    },
                ),
            )
            return PayoutRequestResult(idempotency_key=idempotency_key, payouts=replay)

        provider_wallet = await self._get_provider_wallet(provider_account_id=actor.account_id)
        if provider_wallet is None:
            raise PayoutConflictError("provider wallet address is not configured")

        pending = await self._payout_repo.list_pending()
        if pending:
            raise PayoutConflictError("payouts pending reconciliation")

        ready_payouts = await self._claim_ready_payouts(
            provider_account_id=actor.account_id,
            destination_wallet=provider_wallet,
            idempotency_key=idempotency_key,
        )
        if not ready_payouts:
            raise PayoutConflictError("no ready payouts available")

        logger.info(
            "provider payout request claimed",
            extra=build_event_context(
                "payout.request_claimed",
                **{
                    PROVIDER_ACCOUNT_ID_FIELD: actor.account_id,
                    PAYOUT_COUNT_FIELD: len(ready_payouts),
                },
            ),
        )
        await self._send_claimed_payouts(ready_payouts)
        recorded_payouts = await self._payout_repo.list_for_provider_request(
            provider_account_id=actor.account_id,
            request_idempotency_key=idempotency_key,
        )
        return PayoutRequestResult(idempotency_key=idempotency_key, payouts=recorded_payouts)

    async def reconcile_pending_payouts(self) -> list[Payout]:
        pending_payouts = await self._payout_repo.list_pending()
        if not pending_payouts:
            return []
        logger.info(
            "provider payout reconciliation started",
            extra=build_event_context(
                "payout.reconciliation_started",
                **{
                    PAYOUT_COUNT_FIELD: len(pending_payouts),
                },
            ),
        )
        await self._send_claimed_payouts(pending_payouts)
        return pending_payouts

    async def _claim_ready_payouts(
        self,
        *,
        provider_account_id: int,
        destination_wallet: str,
        idempotency_key: str,
    ) -> list[Payout]:
        if self._payout_executor is None:
            raise PayoutConflictError("payout executor is not configured")
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _PAYOUT_TREASURY_LOCK_KEY},
        )
        payouts = await self._payout_repo.list_ready_for_provider_for_update(
            provider_account_id=provider_account_id,
        )
        if not payouts:
            await self._session.rollback()
            return []
        current_nonce = await self._payout_executor.current_nonce()
        max_chain_nonce = await self._payout_repo.get_max_chain_nonce()
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
            payout.destination_wallet = destination_wallet
            payout.request_idempotency_key = idempotency_key
            payout.chain_nonce = next_nonce
            payout.prepared_raw_transaction = _extract_raw_transaction(prepared)
            payout.transfer_reference = _extract_reference(prepared)
            payout.status = PayoutStatus.PENDING
            payout.attempt_count += 1
            payout.failure_code = None
            payout.error_message = None
            next_nonce += 1
        await self._session.flush()
        await self._session.commit()
        return payouts

    async def _send_claimed_payouts(self, payouts: list[Payout]) -> None:
        if self._payout_executor is None:
            raise PayoutConflictError("payout executor is not configured")

        for payout in payouts:
            raw_transaction = payout.prepared_raw_transaction
            reference = payout.transfer_reference
            if raw_transaction is None or reference is None:
                payout.status = PayoutStatus.FAILED
                payout.failure_code = PayoutFailureCode.EXECUTOR_ERROR
                payout.error_message = "prepared payout transaction is incomplete"
                await self._session.commit()
                continue
            try:
                outcome = await self._payout_executor.send_prepared_payout(
                    raw_transaction=raw_transaction,
                    reference=reference,
                )
            except (PayoutExecutionError, RuntimeError, ValueError) as exc:
                payout.status = PayoutStatus.PENDING
                payout.failure_code = PayoutFailureCode.EXECUTOR_ERROR
                payout.error_message = str(exc)
                logger.error(
                    "provider payout pending reconciliation",
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
            payout.transfer_reference = _extract_reference(outcome) or payout.transfer_reference
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


def _extract_reference(outcome: dict[str, object]) -> str | None:
    for key in ("reference", "transaction"):
        value = outcome.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_raw_transaction(outcome: dict[str, object]) -> str | None:
    value = outcome.get("raw_transaction")
    if isinstance(value, str) and value:
        return value
    return None
