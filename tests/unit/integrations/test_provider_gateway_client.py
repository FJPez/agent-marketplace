import httpx
import pytest
from httpx import Response

from app.core.enums import InvocationFailureReason
from app.integrations.provider_gateway.client import (
    ProviderGatewayClient,
    ProviderGatewayProtocolError,
    ProviderGatewayTimeoutError,
    ProviderGatewayTransportError,
)
from app.integrations.provider_gateway.signing import HmacAuthConfig

pytestmark = [pytest.mark.asyncio]

AUTH = HmacAuthConfig(key_id="gateway-key", secret="super-secret")
LOOPBACK_BASE_URL = "http://127.0.0.1:9000"
# Plain HTTP to a public address is refused in every environment.
UNSAFE_BASE_URL = "http://198.51.100.10:9000"
PAYLOAD: dict[str, object] = {"text": "hello"}


class FakeHttpClient:
    """Stands in for the outbound http client, the gateway's only external I/O."""

    def __init__(self, outcome: Response | Exception | None = None) -> None:
        self.outcome = outcome
        self.calls: list[str] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: object,
        headers: dict[str, str],
        **kwargs: object,
    ) -> Response:
        self.calls.append(f"{method} {url}")
        if self.outcome is None:
            raise AssertionError("no fake outcome configured")
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def aclose(self) -> None:
        return None


async def test_invoke_returns_a_successful_response_with_its_parsed_payload() -> None:
    http_client = FakeHttpClient(Response(status_code=200, json={"result": "bonjour"}))

    response = await ProviderGatewayClient(http_client).invoke(
        base_url=LOOPBACK_BASE_URL,
        path="/invoke",
        http_method="POST",
        payload=PAYLOAD,
        request_hash="a" * 64,
        invocation_id=42,
        timeout_seconds=30,
        auth=AUTH,
    )

    assert response.ok
    assert response.status_code == 200
    assert response.payload == {"result": "bonjour"}
    assert len(http_client.calls) == 1


async def test_invoke_returns_a_non_success_response_as_a_value_without_a_payload() -> None:
    http_client = FakeHttpClient(Response(status_code=503, json={"detail": "down"}))

    response = await ProviderGatewayClient(http_client).invoke(
        base_url=LOOPBACK_BASE_URL,
        path="/invoke",
        http_method="POST",
        payload=PAYLOAD,
        request_hash="a" * 64,
        invocation_id=42,
        timeout_seconds=30,
        auth=AUTH,
    )

    assert not response.ok
    assert response.status_code == 503
    assert response.payload is None


async def test_invoke_rejects_a_success_response_whose_body_is_not_json() -> None:
    http_client = FakeHttpClient(Response(status_code=200, text="not json at all"))

    with pytest.raises(ProviderGatewayProtocolError, match="upstream returned invalid json") as err:
        await ProviderGatewayClient(http_client).invoke(
            base_url=LOOPBACK_BASE_URL,
            path="/invoke",
            http_method="POST",
            payload=PAYLOAD,
            request_hash="a" * 64,
            invocation_id=42,
            timeout_seconds=30,
            auth=AUTH,
        )

    assert err.value.failure_reason is InvocationFailureReason.UPSTREAM_RESPONSE
    assert err.value.upstream_status_code == 200


async def test_invoke_raises_a_timeout_error_when_the_upstream_does_not_answer() -> None:
    http_client = FakeHttpClient(httpx.TimeoutException("boom"))

    with pytest.raises(ProviderGatewayTimeoutError, match="upstream request timed out") as err:
        await ProviderGatewayClient(http_client).invoke(
            base_url=LOOPBACK_BASE_URL,
            path="/invoke",
            http_method="POST",
            payload=PAYLOAD,
            request_hash="a" * 64,
            invocation_id=42,
            timeout_seconds=30,
            auth=AUTH,
        )

    assert err.value.failure_reason is InvocationFailureReason.UPSTREAM_TIMEOUT
    assert err.value.upstream_status_code is None


async def test_invoke_raises_a_transport_error_when_the_connection_fails() -> None:
    http_client = FakeHttpClient(httpx.ConnectError("refused"))

    with pytest.raises(ProviderGatewayTransportError, match="upstream request failed") as err:
        await ProviderGatewayClient(http_client).invoke(
            base_url=LOOPBACK_BASE_URL,
            path="/invoke",
            http_method="POST",
            payload=PAYLOAD,
            request_hash="a" * 64,
            invocation_id=42,
            timeout_seconds=30,
            auth=AUTH,
        )

    assert err.value.failure_reason is InvocationFailureReason.UPSTREAM_TRANSPORT
    assert err.value.upstream_status_code is None


async def test_invoke_refuses_an_unsafe_target_without_contacting_it() -> None:
    http_client = FakeHttpClient()

    with pytest.raises(
        ProviderGatewayTransportError, match="upstream target is not allowed"
    ) as err:
        await ProviderGatewayClient(http_client).invoke(
            base_url=UNSAFE_BASE_URL,
            path="/invoke",
            http_method="POST",
            payload=PAYLOAD,
            request_hash="a" * 64,
            invocation_id=42,
            timeout_seconds=30,
            auth=AUTH,
        )

    assert err.value.failure_reason is InvocationFailureReason.UPSTREAM_TRANSPORT
    assert http_client.calls == []
