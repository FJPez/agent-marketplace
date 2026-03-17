from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.core.enums import PayoutStatus
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
    from app.core.config import Settings
    from app.db.models import Account, Payout

logger = get_logger(__name__)


class PayoutStore(Protocol):
    def add(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        destination_wallet: str,
        amount_minor: int,
        currency: str,
        network: str,
        status: PayoutStatus,
        transfer_reference: str | None = None,
        error_message: str | None = None,
        attempt_count: int = 1,
    ) -> Payout: ...

    async def get_by_payment_attempt_id(self, *, payment_attempt_id: int) -> Payout | None: ...

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
    ) -> PayoutSummary | None: ...


class AccountStore(Protocol):
    async def get(self, account_id: int) -> Account | None: ...


class PayoutService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        payout_repo: PayoutStore | None = None,
        account_repo: AccountStore | None = None,
        settings: Settings | None = None,
        payout_executor: SupportsPayoutExecutor | None = None,
    ) -> None:
        self._session = session
        if payout_repo is None:
            self._payout_repo = PayoutRepository(session)
        else:
            self._payout_repo = payout_repo
        if account_repo is None:
            self._account_repo = AccountRepository(session)
        else:
            self._account_repo = account_repo
        self._settings = settings
        self._payout_executor = payout_executor

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

    async def get_provider_payout_summary(self, actor: ActorContext) -> PayoutSummary | None:
        return await self._payout_repo.summarize_for_provider(provider_account_id=actor.account_id)

    async def record_provider_payout(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        gross_amount_minor: int,
        currency: str,
    ) -> None:
        if self._settings is None:
            msg = "settings are required for payout execution"
            raise RuntimeError(msg)
        payout = await self._payout_repo.get_by_payment_attempt_id(
            payment_attempt_id=payment_attempt_id,
        )
        if payout is not None:
            return
        _, provider_amount_minor = split_paid_invocation_amount(gross_amount_minor)
        provider_wallet = await self._get_provider_wallet(provider_account_id=provider_account_id)
        if provider_wallet is None:
            self._payout_repo.add(
                provider_account_id=provider_account_id,
                service_id=service_id,
                invocation_id=invocation_id,
                payment_attempt_id=payment_attempt_id,
                destination_wallet="",
                amount_minor=provider_amount_minor,
                currency=currency,
                network=self._settings.x402_network,
                status=PayoutStatus.FAILED,
                error_message="provider wallet address is not configured",
            )
            await self._session.commit()
            return
        payout = self._payout_repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            destination_wallet=provider_wallet,
            amount_minor=provider_amount_minor,
            currency=currency,
            network=self._settings.x402_network,
            status=PayoutStatus.READY,
            attempt_count=0,
        )
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
        if provider_amount_minor <= 0:
            payout.status = PayoutStatus.FAILED
            payout.error_message = "provider payout amount must be positive"
            await self._session.commit()
            return
        if not self._settings.payouts_enabled or self._payout_executor is None:
            await self._session.commit()
            return
        payout.status = PayoutStatus.PENDING
        payout.attempt_count += 1
        await self._session.commit()
        try:
            outcome = await self._payout_executor.send_payout(
                destination_wallet=provider_wallet,
                amount_minor=provider_amount_minor,
                idempotency_key=f"payment-attempt:{payment_attempt_id}",
            )
        except (PayoutExecutionError, ValueError, RuntimeError) as exc:
            payout.status = PayoutStatus.FAILED
            payout.error_message = str(exc)
            logger.error(
                "provider payout failed",
                extra=build_event_context(
                    "payout.failed",
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
            await self._session.commit()
            return
        payout.status = PayoutStatus.SENT
        payout.transfer_reference = _extract_reference(outcome)
        payout.error_message = None
        logger.info(
            "provider payout sent",
            extra=build_event_context(
                "payout.sent",
                **{
                    PAYMENT_ATTEMPT_ID_FIELD: payment_attempt_id,
                    PAYOUT_ID_FIELD: payout.id,
                    PAYOUT_STATUS_FIELD: payout.status.value,
                    PROVIDER_ACCOUNT_ID_FIELD: provider_account_id,
                    INVOCATION_ID_FIELD: invocation_id,
                    SERVICE_ID_FIELD: service_id,
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
