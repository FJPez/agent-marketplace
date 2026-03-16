from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from cdp.auth.utils.jwt import JwtOptions, generate_jwt
from x402 import parse_payment_payload
from x402.http import AuthHeaders, FacilitatorConfig
from x402.http.facilitator_client import HTTPFacilitatorClient

from app.integrations.x402.models import to_payment_requirements

if TYPE_CHECKING:
    from httpx import AsyncClient


logger = logging.getLogger(__name__)
_AUTH_STATUS_CODE_PATTERN = re.compile(r"\((?P<status>\d{3})\)")
_CDP_FACILITATOR_HOSTS = {"api.cdp.coinbase.com"}


class FacilitatorConfigError(RuntimeError):
    pass


class FacilitatorAuthError(Exception):
    pass


class FacilitatorUnavailableError(Exception):
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
        except Exception as exc:
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
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        payload = parse_payment_payload(payment_payload)
        requirement = to_payment_requirements(payment_requirement)
        try:
            result = await self._client.verify(payload, requirement)
            return result.model_dump(by_alias=True, exclude_none=True)
        except FacilitatorAuthError as exc:
            logger.warning(
                "Facilitator authentication failed during verify for %s: %s",
                self._identifier,
                exc,
            )
            raise FacilitatorAuthError("facilitator authentication failed") from exc
        except Exception as exc:
            if _has_auth_status_code(exc):
                logger.warning(
                    "Facilitator returned an authentication failure during verify for %s: %s",
                    self._identifier,
                    exc,
                )
                raise FacilitatorAuthError("facilitator authentication failed") from exc
            logger.warning(
                "Facilitator verify failed for %s: %s",
                self._identifier,
                exc,
            )
            raise FacilitatorUnavailableError("facilitator unavailable") from exc

    async def settle(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        payload = parse_payment_payload(payment_payload)
        requirement = to_payment_requirements(payment_requirement)
        try:
            result = await self._client.settle(payload, requirement)
            return result.model_dump(by_alias=True, exclude_none=True)
        except FacilitatorAuthError as exc:
            logger.warning(
                "Facilitator authentication failed during settle for %s: %s",
                self._identifier,
                exc,
            )
            raise FacilitatorAuthError("facilitator authentication failed") from exc
        except Exception as exc:
            if _has_auth_status_code(exc):
                logger.warning(
                    "Facilitator returned an authentication failure during settle for %s: %s",
                    self._identifier,
                    exc,
                )
                raise FacilitatorAuthError("facilitator authentication failed") from exc
            logger.warning(
                "Facilitator settle failed for %s: %s",
                self._identifier,
                exc,
            )
            raise FacilitatorUnavailableError("facilitator unavailable") from exc


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
    return urlsplit(url).hostname in _CDP_FACILITATOR_HOSTS


def _has_auth_status_code(exc: Exception) -> bool:
    status_code = _extract_status_code(exc)
    return status_code in {401, 403}


def _extract_status_code(exc: Exception) -> int | None:
    match = _AUTH_STATUS_CODE_PATTERN.search(str(exc))
    if match is None:
        return None
    return int(match.group("status"))
