import pytest
from httpx import ConnectError, ReadTimeout
from x402.http.facilitator_client import FacilitatorResponseError
from x402.schemas import SettleResponse, VerifyResponse

from app.integrations.x402.facilitator_client import (
    CdpFacilitatorAuthProvider,
    FacilitatorAuthError,
    FacilitatorClient,
    FacilitatorConfigError,
    FacilitatorProtocolError,
    FacilitatorTimeoutError,
    FacilitatorTransportError,
)
from app.integrations.x402.models import PaymentPayload, PaymentRequirement


class FakeSdkClient:
    """Stands in for the x402 SDK client: returns one answer, or raises one error."""

    def __init__(self, *, answer: object = None, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error

    async def verify(self, payload: object, requirement: object) -> object:
        return self._respond()

    async def settle(self, payload: object, requirement: object) -> object:
        return self._respond()

    def _respond(self) -> object:
        if self.error is not None:
            raise self.error
        return self.answer


def build_client(*, answer: object = None, error: Exception | None = None) -> FacilitatorClient:
    client = FacilitatorClient(url="https://facilitator.internal")
    client._client = FakeSdkClient(answer=answer, error=error)
    return client


def test_cdp_auth_provider_generates_endpoint_specific_bearer_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, str, str, str]] = []

    class FakeJwtOptions:
        def __init__(
            self,
            *,
            api_key_id: str,
            api_key_secret: str,
            request_method: str,
            request_host: str,
            request_path: str,
        ) -> None:
            self.api_key_id = api_key_id
            self.api_key_secret = api_key_secret
            self.request_method = request_method
            self.request_host = request_host
            self.request_path = request_path

    def fake_generate_jwt(options: FakeJwtOptions) -> str:
        recorded.append(
            (
                options.api_key_id,
                options.request_method,
                options.request_host,
                options.request_path,
            )
        )
        return f"jwt-{options.request_method}-{options.request_path}"

    monkeypatch.setattr(
        "app.integrations.x402.facilitator_client.JwtOptions",
        FakeJwtOptions,
    )
    monkeypatch.setattr(
        "app.integrations.x402.facilitator_client.generate_jwt",
        fake_generate_jwt,
    )
    provider = CdpFacilitatorAuthProvider(
        api_key_id="key-id",
        api_key_secret="secret",
        facilitator_url="https://api.cdp.coinbase.com/platform/v2/x402",
    )

    headers = provider.get_auth_headers()

    assert headers.supported == {"Authorization": "Bearer jwt-GET-/platform/v2/x402/supported"}
    assert headers.verify == {"Authorization": "Bearer jwt-POST-/platform/v2/x402/verify"}
    assert headers.settle == {"Authorization": "Bearer jwt-POST-/platform/v2/x402/settle"}
    assert recorded == [
        ("key-id", "POST", "api.cdp.coinbase.com", "/platform/v2/x402/verify"),
        ("key-id", "POST", "api.cdp.coinbase.com", "/platform/v2/x402/settle"),
        ("key-id", "GET", "api.cdp.coinbase.com", "/platform/v2/x402/supported"),
    ]


def test_cdp_auth_provider_reports_a_failed_token_as_an_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(options: object) -> str:
        raise ValueError("Failed to generate JWT: bad key")

    monkeypatch.setattr("app.integrations.x402.facilitator_client.generate_jwt", fail)
    provider = CdpFacilitatorAuthProvider(
        api_key_id="key-id",
        api_key_secret="secret",
        facilitator_url="https://api.cdp.coinbase.com/platform/v2/x402",
    )

    with pytest.raises(FacilitatorAuthError, match="failed to generate facilitator auth token"):
        provider.get_auth_headers()


def test_facilitator_client_requires_cdp_credentials_for_cdp_url() -> None:
    with pytest.raises(
        FacilitatorConfigError,
        match="APP_X402_CDP_API_KEY_ID and APP_X402_CDP_API_KEY_SECRET are required",
    ):
        FacilitatorClient(url="https://api.cdp.coinbase.com/platform/v2/x402")


def test_facilitator_client_allows_non_cdp_url_without_cdp_credentials() -> None:
    client = FacilitatorClient(url="https://facilitator.internal")

    assert client._client is not None


def test_facilitator_client_wires_cdp_auth_provider_when_credentials_present() -> None:
    client = FacilitatorClient(
        url="https://api.cdp.coinbase.com/platform/v2/x402",
        cdp_api_key_id="key-id",
        cdp_api_key_secret="secret",
    )

    assert isinstance(client._client._auth_provider, CdpFacilitatorAuthProvider)


@pytest.mark.asyncio
async def test_verify_returns_an_accepted_outcome(
    payment_requirement: PaymentRequirement,
    payment_payload: PaymentPayload,
) -> None:
    client = build_client(
        answer=VerifyResponse.model_validate({"isValid": True, "payer": "0xpayer"}),
    )

    outcome = await client.verify(requirement=payment_requirement, payload=payment_payload)

    assert outcome.accepted is True
    assert outcome.payer == "0xpayer"
    assert outcome.checked_by == "facilitator"


@pytest.mark.asyncio
async def test_verify_returns_a_rejection_as_a_value(
    payment_requirement: PaymentRequirement,
    payment_payload: PaymentPayload,
) -> None:
    client = build_client(
        answer=VerifyResponse.model_validate(
            {
                "isValid": False,
                "invalidReason": "insufficient_funds",
                "invalidMessage": "the payer cannot cover the amount",
            }
        ),
    )

    outcome = await client.verify(requirement=payment_requirement, payload=payment_payload)

    assert outcome.accepted is False
    assert outcome.reason == "insufficient_funds"
    assert outcome.message == "the payer cannot cover the amount"


@pytest.mark.asyncio
async def test_settle_returns_a_settled_outcome(
    payment_requirement: PaymentRequirement,
    payment_payload: PaymentPayload,
) -> None:
    client = build_client(
        answer=SettleResponse.model_validate(
            {
                "success": True,
                "transaction": "0xsettled",
                "network": "eip155:84532",
                "payer": "0xpayer",
                "amount": "5000000",
            }
        ),
    )

    outcome = await client.settle(requirement=payment_requirement, payload=payment_payload)

    assert outcome.success is True
    assert outcome.reference == "0xsettled"
    assert outcome.network == "eip155:84532"
    assert outcome.amount == "5000000"


@pytest.mark.asyncio
async def test_settle_returns_a_rejection_as_a_value(
    payment_requirement: PaymentRequirement,
    payment_payload: PaymentPayload,
) -> None:
    client = build_client(
        answer=SettleResponse.model_validate(
            {
                "success": False,
                "transaction": "",
                "network": "eip155:84532",
                "errorReason": "insufficient_funds",
            }
        ),
    )

    outcome = await client.settle(requirement=payment_requirement, payload=payment_payload)

    assert outcome.success is False
    assert outcome.error_reason == "insufficient_funds"


@pytest.mark.asyncio
async def test_verify_maps_a_read_timeout_to_a_timeout_error(
    payment_requirement: PaymentRequirement,
    payment_payload: PaymentPayload,
) -> None:
    client = build_client(error=ReadTimeout("read timed out"))

    with pytest.raises(FacilitatorTimeoutError, match="facilitator verify failed: read timed out"):
        await client.verify(requirement=payment_requirement, payload=payment_payload)


@pytest.mark.asyncio
async def test_verify_maps_a_connection_failure_to_a_transport_error(
    payment_requirement: PaymentRequirement,
    payment_payload: PaymentPayload,
) -> None:
    client = build_client(error=ConnectError("connection refused"))

    with pytest.raises(
        FacilitatorTransportError,
        match="facilitator verify failed: connection refused",
    ):
        await client.verify(requirement=payment_requirement, payload=payment_payload)


@pytest.mark.asyncio
async def test_verify_maps_an_unauthorized_status_to_an_auth_error(
    payment_requirement: PaymentRequirement,
    payment_payload: PaymentPayload,
) -> None:
    client = build_client(error=ValueError("Facilitator verify failed (401): unauthorized"))

    with pytest.raises(FacilitatorAuthError, match="facilitator authentication failed"):
        await client.verify(requirement=payment_requirement, payload=payment_payload)


@pytest.mark.asyncio
async def test_settle_maps_a_server_error_status_to_a_protocol_error(
    payment_requirement: PaymentRequirement,
    payment_payload: PaymentPayload,
) -> None:
    client = build_client(error=ValueError("Facilitator settle failed (500): boom"))

    with pytest.raises(FacilitatorProtocolError, match="facilitator settle failed"):
        await client.settle(requirement=payment_requirement, payload=payment_payload)


@pytest.mark.asyncio
async def test_settle_maps_an_unreadable_response_to_a_protocol_error(
    payment_requirement: PaymentRequirement,
    payment_payload: PaymentPayload,
) -> None:
    client = build_client(
        error=FacilitatorResponseError("Facilitator settle returned invalid data: <empty response>")
    )

    with pytest.raises(FacilitatorProtocolError, match="returned invalid data"):
        await client.settle(requirement=payment_requirement, payload=payment_payload)
