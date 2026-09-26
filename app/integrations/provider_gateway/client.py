from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Protocol, runtime_checkable

import httpx
from httpx import Response

from app.core.enums import InvocationFailureReason
from app.core.upstream_targets import UnsafeUpstreamTargetError, validate_upstream_base_url
from app.integrations.provider_gateway.signing import HmacAuthConfig, build_signed_headers


class ProviderGatewayError(Exception):
    """Raised only when no valid protocol outcome could be observed at all."""

    def __init__(
        self,
        message: str,
        *,
        failure_reason: InvocationFailureReason,
        upstream_status_code: int | None = None,
    ) -> None:
        self.failure_reason = failure_reason
        self.upstream_status_code = upstream_status_code
        super().__init__(message)


class ProviderGatewayTimeoutError(ProviderGatewayError):
    def __init__(self, message: str) -> None:
        super().__init__(message, failure_reason=InvocationFailureReason.UPSTREAM_TIMEOUT)


class ProviderGatewayTransportError(ProviderGatewayError):
    def __init__(self, message: str) -> None:
        super().__init__(message, failure_reason=InvocationFailureReason.UPSTREAM_TRANSPORT)


class ProviderGatewayProtocolError(ProviderGatewayError):
    def __init__(self, message: str, *, upstream_status_code: int) -> None:
        super().__init__(
            message,
            failure_reason=InvocationFailureReason.UPSTREAM_RESPONSE,
            upstream_status_code=upstream_status_code,
        )


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    status_code: int
    payload: object | None

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


@runtime_checkable
class SupportsRequest(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        json: object,
        headers: dict[str, str],
        **kwargs: object,
    ) -> Response: ...

    async def aclose(self) -> None: ...


class ProviderGatewayClient:
    def __init__(self, http_client: SupportsRequest) -> None:
        self._http_client = http_client

    async def invoke(
        self,
        *,
        base_url: str,
        path: str,
        http_method: str,
        payload: object,
        request_hash: str,
        invocation_id: int,
        timeout_seconds: int,
        auth: HmacAuthConfig,
    ) -> ProviderResponse:
        """Return the upstream's answer, raising only when no usable answer was observed."""
        timestamp = str(int(time()))
        headers = build_signed_headers(
            key_id=auth.key_id,
            secret=auth.secret,
            http_method=http_method,
            path=path,
            request_hash=request_hash,
            invocation_id=invocation_id,
            timestamp=timestamp,
        )
        headers["Content-Type"] = "application/json"
        try:
            validated_base_url = validate_upstream_base_url(base_url)
        except UnsafeUpstreamTargetError as exc:
            # A refused target is reported the same way as a transport fault: no usable
            # answer was observed, and the request may or may not have reached the upstream.
            raise ProviderGatewayTransportError(str(exc)) from exc

        url = f"{validated_base_url.rstrip('/')}{path}"
        try:
            response = await self._http_client.request(
                http_method,
                url,
                json=payload,
                headers=headers,
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderGatewayTimeoutError("upstream request timed out") from exc
        except httpx.RequestError as exc:
            raise ProviderGatewayTransportError("upstream request failed") from exc

        # A non-2xx body is the provider's error page, not a protocol payload, so it is
        # never parsed; the status code alone carries the outcome.
        if not (200 <= response.status_code < 300):
            return ProviderResponse(status_code=response.status_code, payload=None)

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderGatewayProtocolError(
                "upstream returned invalid json",
                upstream_status_code=response.status_code,
            ) from exc
        return ProviderResponse(status_code=response.status_code, payload=body)
