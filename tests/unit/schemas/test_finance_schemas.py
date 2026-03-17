from datetime import UTC, datetime

from app.core.enums import PayoutStatus
from app.db.models import Payout
from app.schemas.finance import ProviderPayoutResponse


def test_provider_payout_response_truncates_error_message() -> None:
    payout = Payout(
        id=1,
        provider_account_id=2,
        service_id=3,
        invocation_id=4,
        payment_attempt_id=5,
        destination_wallet="0x00000000000000000000000000000000000000aa",
        amount_minor=450,
        currency="USDC",
        network="base-sepolia",
        status=PayoutStatus.FAILED,
        transfer_reference=None,
        error_message="x" * 250,
        attempt_count=2,
    )
    payout.created_at = datetime.now(UTC)
    payout.updated_at = datetime.now(UTC)

    response = ProviderPayoutResponse.from_model(payout)

    assert response.error_message == "x" * 200
