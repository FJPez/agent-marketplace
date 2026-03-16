import pytest

from app.integrations.x402.facilitator_client import (
    CdpFacilitatorAuthProvider,
    FacilitatorAuthError,
    FacilitatorClient,
    FacilitatorConfigError,
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


class _FakeAuthErrorSdkClient:
    async def verify(self, payload: object, requirement: object) -> object:
        _ = payload
        _ = requirement
        raise ValueError("Facilitator verify failed (401): unauthorized")

    async def settle(self, payload: object, requirement: object) -> object:
        _ = payload
        _ = requirement
        raise ValueError("Facilitator settle failed (403): forbidden")


def test_cdp_auth_provider_generates_endpoint_specific_bearer_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, str, str, str]] = []

    class _FakeJwtOptions:
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

    def fake_generate_jwt(options: _FakeJwtOptions) -> str:
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
        _FakeJwtOptions,
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

    with pytest.raises(
        FacilitatorUnavailableError,
        match=r"facilitator verify failed: RuntimeError\('boom'\)|facilitator verify failed: boom",
    ):
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

    with pytest.raises(
        FacilitatorUnavailableError,
        match=r"facilitator settle failed: RuntimeError\('boom'\)|facilitator settle failed: boom",
    ):
        await client.settle(
            payment_requirement={"amount_minor": 500},
            payment_payload={"authorization": {"nonce": "payment-1"}},
        )


@pytest.mark.asyncio
async def test_verify_wraps_sdk_auth_failures_as_facilitator_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FacilitatorClient(
        url="https://api.cdp.coinbase.com/platform/v2/x402",
        cdp_api_key_id="key-id",
        cdp_api_key_secret="secret",
    )
    client._client = _FakeAuthErrorSdkClient()
    monkeypatch.setattr(
        "app.integrations.x402.facilitator_client.parse_payment_payload",
        lambda payload: payload,
    )
    monkeypatch.setattr(
        "app.integrations.x402.facilitator_client.to_payment_requirements",
        lambda requirement: requirement,
    )

    with pytest.raises(FacilitatorAuthError, match="facilitator authentication failed"):
        await client.verify(
            payment_requirement={"amount_minor": 500},
            payment_payload={"authorization": {"nonce": "payment-1"}},
        )
