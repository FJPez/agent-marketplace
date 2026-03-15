import pytest

from app.integrations.x402.facilitator_client import (
    FacilitatorClient,
    FacilitatorUnavailableError,
)


class _FakeSdkClient:
    async def verify(self, payload: object, requirement: object) -> object:
        _ = payload
        _ = requirement
        raise RuntimeError("boom")

    async def settle(self, payload: object, requirement: object) -> object:
        _ = payload
        _ = requirement
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_verify_wraps_sdk_failures_as_facilitator_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FacilitatorClient(url="https://facilitator.internal")
    client._client = _FakeSdkClient()
    monkeypatch.setattr(
        "app.integrations.x402.facilitator_client.parse_payment_payload",
        lambda payload: payload,
    )
    monkeypatch.setattr(
        "app.integrations.x402.facilitator_client.to_payment_requirements",
        lambda requirement: requirement,
    )

    with pytest.raises(FacilitatorUnavailableError, match="facilitator unavailable"):
        await client.verify(
            payment_requirement={"amount_minor": 500},
            payment_payload={"authorization": {"nonce": "payment-1"}},
        )


@pytest.mark.asyncio
async def test_settle_wraps_sdk_failures_as_facilitator_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FacilitatorClient(url="https://facilitator.internal")
    client._client = _FakeSdkClient()
    monkeypatch.setattr(
        "app.integrations.x402.facilitator_client.parse_payment_payload",
        lambda payload: payload,
    )
    monkeypatch.setattr(
        "app.integrations.x402.facilitator_client.to_payment_requirements",
        lambda requirement: requirement,
    )

    with pytest.raises(FacilitatorUnavailableError, match="facilitator unavailable"):
        await client.settle(
            payment_requirement={"amount_minor": 500},
            payment_payload={"authorization": {"nonce": "payment-1"}},
        )
