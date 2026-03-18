from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Protocol, runtime_checkable

import httpx
from httpx import Response

from app.core.upstream_targets import UnsafeUpstreamTargetError, validate_upstream_base_url
from app.integrations.provider_gateway.signing import HmacAuthConfig, build_signed_headers


class ProviderGatewayTimeoutError(Exception):
    pass


class ProviderGatewayTransportError(Exception):
    pass


class ProviderGatewayTargetError(Exception):
    pass


class ProviderGatewayResponseError(Exception):
    def __init__(self, message: str, *, upstream_status_code: int | None) -> None:
        self.upstream_status_code = upstream_status_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ProviderGatewayResult:
    status_code: int
    payload: object


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
    ) -> ProviderGatewayResult:
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
            raise ProviderGatewayTargetError(str(exc)) from exc

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

        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderGatewayResponseError(
                "upstream request failed",
                upstream_status_code=response.status_code,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderGatewayResponseError(
                "upstream returned invalid json",
                upstream_status_code=response.status_code,
            ) from exc
        return ProviderGatewayResult(status_code=response.status_code, payload=body)
