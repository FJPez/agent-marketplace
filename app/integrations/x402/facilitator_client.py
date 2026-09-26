from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from cdp.auth.utils.jwt import JwtOptions, generate_jwt
from httpx import RequestError, TimeoutException
from pydantic import ValidationError
from x402 import parse_payment_payload
from x402.http import AuthHeaders, FacilitatorConfig
from x402.http.facilitator_client import FacilitatorResponseError, HTTPFacilitatorClient

from app.integrations.x402.models import SettleOutcome, VerifyOutcome

if TYPE_CHECKING:
    from httpx import AsyncClient

    from app.integrations.x402.models import PaymentPayload, PaymentRequirement


logger = logging.getLogger(__name__)
AUTH_STATUS_CODE_PATTERN = re.compile(r"\((?P<status>\d{3})\)")
CDP_FACILITATOR_HOSTS = {"api.cdp.coinbase.com"}


class FacilitatorError(Exception):
    """No usable outcome could be obtained from the facilitator."""


class FacilitatorConfigError(FacilitatorError):
    pass


class FacilitatorAuthError(FacilitatorError):
    pass


class FacilitatorTimeoutError(FacilitatorError):
    pass


class FacilitatorTransportError(FacilitatorError):
    pass


class FacilitatorProtocolError(FacilitatorError):
    pass


class CdpFacilitatorAuthProvider:
    def __init__(
        self,
        *,
        api_key_id: str,
        api_key_secret: str,
        facilitator_url: str,
    ) -> None:
        parsed_url = urlsplit(facilitator_url)
        if not parsed_url.netloc:
            msg = "facilitator URL must include a host"
            raise FacilitatorConfigError(msg)
        self._api_key_id = api_key_id
        self._api_key_secret = api_key_secret
        self._request_host = parsed_url.netloc
        self._request_base_path = parsed_url.path.rstrip("/")

    def get_auth_headers(self) -> AuthHeaders:
        try:
            return AuthHeaders(
                verify=self._build_bearer_headers("POST", "verify"),
                settle=self._build_bearer_headers("POST", "settle"),
                supported=self._build_bearer_headers("GET", "supported"),
            )
        except (ValueError, TypeError) as exc:
            raise FacilitatorAuthError("failed to generate facilitator auth token") from exc

    def _build_bearer_headers(self, request_method: str, endpoint: str) -> dict[str, str]:
        token = generate_jwt(
            JwtOptions(
                api_key_id=self._api_key_id,
                api_key_secret=self._api_key_secret,
                request_method=request_method,
                request_host=self._request_host,
                request_path=_build_request_path(self._request_base_path, endpoint),
            )
        )
        return {"Authorization": f"Bearer {token}"}


class FacilitatorClient:
    """Turns facilitator calls into typed outcomes.

    `/verify` only asks the facilitator whether the payment would be accepted: it moves no
    funds and executes nothing on chain, so a verified attempt owes the payer nothing yet.
    """

    def __init__(
        self,
        *,
        url: str,
        http_client: AsyncClient | None = None,
        cdp_api_key_id: str | None = None,
        cdp_api_key_secret: str | None = None,
    ) -> None:
        self._identifier = url.rstrip("/")
        self._client = HTTPFacilitatorClient(
            FacilitatorConfig(
                url=url,
                http_client=http_client,
                auth_provider=_build_auth_provider(
                    url=url,
                    cdp_api_key_id=cdp_api_key_id,
                    cdp_api_key_secret=cdp_api_key_secret,
                ),
            )
        )

    async def verify(
        self,
        *,
        requirement: PaymentRequirement,
        payload: PaymentPayload,
    ) -> VerifyOutcome:
        sdk_payload = parse_payment_payload(payload.wire)
        sdk_requirement = requirement.to_sdk()
        try:
            response = await self._client.verify(sdk_payload, sdk_requirement)
        except (FacilitatorAuthError, RequestError, ValueError) as exc:
            raise self._translate("verify", exc) from exc
        return VerifyOutcome.from_sdk(response)

    async def settle(
        self,
        *,
        requirement: PaymentRequirement,
        payload: PaymentPayload,
    ) -> SettleOutcome:
        sdk_payload = parse_payment_payload(payload.wire)
        sdk_requirement = requirement.to_sdk()
        try:
            response = await self._client.settle(sdk_payload, sdk_requirement)
        except (FacilitatorAuthError, RequestError, ValueError) as exc:
            raise self._translate("settle", exc) from exc
        return SettleOutcome.from_sdk(response)

    def _translate(self, operation: str, exc: Exception) -> FacilitatorError:
        if isinstance(exc, FacilitatorAuthError):
            logger.warning(
                "Facilitator authentication failed during %s for %s: %s",
                operation,
                self._identifier,
                exc,
            )
            return FacilitatorAuthError("facilitator authentication failed")
        if isinstance(exc, TimeoutException):
            logger.warning(
                "Facilitator %s timed out for %s: %s",
                operation,
                self._identifier,
                exc,
            )
            return FacilitatorTimeoutError(_build_failure_message(operation, exc))
        if isinstance(exc, RequestError):
            logger.warning(
                "Facilitator %s could not reach %s: %s",
                operation,
                self._identifier,
                exc,
            )
            return FacilitatorTransportError(_build_failure_message(operation, exc))
        if isinstance(exc, FacilitatorResponseError | ValidationError):
            logger.warning(
                "Facilitator %s returned an unusable response from %s: %s",
                operation,
                self._identifier,
                exc,
            )
            return FacilitatorProtocolError(_build_failure_message(operation, exc))
        # Only the SDK's own bare ValueError carries the upstream status code in its message.
        if _has_auth_status_code(exc):
            logger.warning(
                "Facilitator returned an authentication failure during %s for %s: %s",
                operation,
                self._identifier,
                exc,
            )
            return FacilitatorAuthError("facilitator authentication failed")
        logger.warning(
            "Facilitator %s failed for %s: %s",
            operation,
            self._identifier,
            exc,
        )
        return FacilitatorProtocolError(_build_failure_message(operation, exc))


def _build_auth_provider(
    *,
    url: str,
    cdp_api_key_id: str | None,
    cdp_api_key_secret: str | None,
) -> CdpFacilitatorAuthProvider | None:
    requires_cdp_credentials = (
        _is_cdp_facilitator_url(url) or cdp_api_key_id is not None or cdp_api_key_secret is not None
    )
    if not requires_cdp_credentials:
        return None
    if not cdp_api_key_id or not cdp_api_key_secret:
        msg = (
            "APP_X402_CDP_API_KEY_ID and APP_X402_CDP_API_KEY_SECRET are required "
            "when APP_X402_FACILITATOR_URL points to the CDP facilitator or either "
            "CDP credential is set"
        )
        raise FacilitatorConfigError(msg)
    if not _is_cdp_facilitator_url(url):
        return None
    return CdpFacilitatorAuthProvider(
        api_key_id=cdp_api_key_id,
        api_key_secret=cdp_api_key_secret,
        facilitator_url=url,
    )


def _build_request_path(base_path: str, endpoint: str) -> str:
    if not base_path:
        return f"/{endpoint}"
    return f"{base_path}/{endpoint}"


def _is_cdp_facilitator_url(url: str) -> bool:
    return urlsplit(url).hostname in CDP_FACILITATOR_HOSTS


def _has_auth_status_code(exc: Exception) -> bool:
    return _extract_status_code(exc) in {401, 403}


def _extract_status_code(exc: Exception) -> int | None:
    match = AUTH_STATUS_CODE_PATTERN.search(str(exc))
    if match is None:
        return None
    return int(match.group("status"))


def _build_failure_message(operation: str, exc: Exception) -> str:
    detail = str(exc).strip()
    if detail:
        return f"facilitator {operation} failed: {detail}"
    return "facilitator unavailable"
