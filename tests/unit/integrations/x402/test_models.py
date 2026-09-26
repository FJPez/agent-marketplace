import pytest
from tests.helpers.x402 import PAYMENT_ASSET, payment_signature_header
from x402.http import encode_payment_signature_header
from x402.schemas.v1 import PaymentPayloadV1

from app.integrations.x402.models import (
    InvalidPaymentPayloadError,
    PaymentPayload,
    PaymentRequirement,
    SettleOutcome,
    VerifyOutcome,
)


def test_from_header_prefers_the_authorization_nonce_as_the_identifier() -> None:
    payload = PaymentPayload.from_header(payment_signature_header(payment_identifier="payment-1"))

    assert payload.identifier == "payment-1"
    assert payload.accepted_asset == PAYMENT_ASSET
    assert payload.wire["x402Version"] == 2


def test_from_stored_falls_back_to_the_transaction_then_the_signature() -> None:
    accepted: dict[str, object] = {"accepted": {"asset": PAYMENT_ASSET}}

    from_transaction = PaymentPayload.from_stored(
        {**accepted, "payload": {"authorization": {}, "transaction": "0xabc123"}}
    )
    from_signature = PaymentPayload.from_stored(
        {**accepted, "payload": {"signature": "0xsigned"}},
    )

    assert from_transaction.identifier == "0xabc123"
    assert from_signature.identifier == "0xsigned"


def test_from_header_rejects_a_payload_that_is_not_x402_v2() -> None:
    header_value = encode_payment_signature_header(
        PaymentPayloadV1.model_validate(
            {
                "x402Version": 1,
                "scheme": "exact",
                "network": "base-sepolia",
                "payload": {"authorization": {"nonce": "payment-1"}},
            }
        )
    )

    with pytest.raises(InvalidPaymentPayloadError, match="not a valid x402 v2 payload"):
        PaymentPayload.from_header(header_value)


def test_from_header_rejects_a_header_that_cannot_be_decoded() -> None:
    with pytest.raises(InvalidPaymentPayloadError, match="not a valid x402 v2 payload"):
        PaymentPayload.from_header("not-base64-at-all")


def test_from_stored_rejects_a_payload_without_an_identifier() -> None:
    with pytest.raises(InvalidPaymentPayloadError, match="payment identifier is missing"):
        PaymentPayload.from_stored({"accepted": {"asset": PAYMENT_ASSET}})


def test_matches_compares_the_asset_case_insensitively(
    payment_requirement: PaymentRequirement,
) -> None:
    upper = PaymentPayload.from_header(payment_signature_header(asset=PAYMENT_ASSET.upper()))
    other = PaymentPayload.from_header(
        payment_signature_header(asset="0x00000000000000000000000000000000000000aa")
    )

    assert upper.matches(payment_requirement) is True
    assert other.matches(payment_requirement) is False


def test_payment_requirement_survives_a_json_round_trip(
    payment_requirement: PaymentRequirement,
) -> None:
    stored = payment_requirement.model_dump(mode="json")

    assert PaymentRequirement.model_validate(stored) == payment_requirement


def test_payment_requirement_to_sdk_maps_the_caip2_network_and_the_asset_amount(
    payment_requirement: PaymentRequirement,
) -> None:
    requirements = payment_requirement.to_sdk()

    assert requirements.scheme == "exact"
    assert requirements.network == "eip155:84532"
    assert requirements.asset == PAYMENT_ASSET
    assert requirements.amount == "5000000"
    assert requirements.pay_to == "0x000000000000000000000000000000000000c0de"
    assert requirements.max_timeout_seconds == 300
    assert requirements.extra == {
        "facilitator_url": "https://x402.org/facilitator",
        "network": "base-sepolia",
        "currency": "USD",
        "amount_minor": 500,
        "name": "USDC",
        "version": "2",
    }


def test_verify_outcome_asset_mismatch_is_decided_by_the_resource_server() -> None:
    outcome = VerifyOutcome.asset_mismatch()

    assert outcome.accepted is False
    assert outcome.reason == "asset_mismatch"
    assert outcome.message == "payment asset does not match the requirement"
    assert outcome.payer is None
    assert outcome.checked_by == "resource_server"


def test_settle_outcome_to_sdk_carries_the_settlement_fields() -> None:
    outcome = SettleOutcome(
        success=True,
        transaction="0xsettled",
        network="eip155:84532",
        payer="0xpayer",
        amount="5000000",
    )

    response = outcome.to_sdk()

    assert response.success is True
    assert response.transaction == "0xsettled"
    assert response.network == "eip155:84532"
    assert response.payer == "0xpayer"
    assert response.amount == "5000000"


def test_settle_outcome_to_sdk_rejects_an_outcome_without_a_transaction() -> None:
    outcome = SettleOutcome(success=True, network="eip155:84532")

    with pytest.raises(ValueError, match="cannot be encoded"):
        outcome.to_sdk()
