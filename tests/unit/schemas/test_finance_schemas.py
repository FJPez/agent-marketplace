from datetime import UTC, datetime

from app.core.enums import PayoutFailureCode, PayoutStatus
from app.db.models import Payout
from app.schemas.finance import ProviderPayoutResponse


def test_provider_payout_response_excludes_internal_execution_fields() -> None:
    payout = Payout(
        id=1,
        provider_account_id=2,
        service_id=3,
        invocation_id=4,
        payment_attempt_id=5,
        destination_wallet=None,
        amount_minor=4_500_000,
        currency="USDC",
        network="base-sepolia",
        status=PayoutStatus.FAILED,
        transfer_reference="0xhidden",
        failure_code=PayoutFailureCode.EXECUTOR_ERROR,
        error_message="rpc unavailable",
        attempt_count=2,
    )
    payout.created_at = datetime.now(UTC)
    payout.updated_at = datetime.now(UTC)

    response = ProviderPayoutResponse.from_model(payout)
    payload = response.model_dump()

    assert response.failure_code is PayoutFailureCode.EXECUTOR_ERROR
    assert payload["destination_wallet"] is None
    assert "transfer_reference" not in payload
    assert "error_message" not in payload
