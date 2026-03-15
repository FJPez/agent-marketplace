from __future__ import annotations

from typing import TYPE_CHECKING

from x402 import parse_payment_payload
from x402.http import FacilitatorConfig
from x402.http.facilitator_client import HTTPFacilitatorClient

from app.integrations.x402.models import to_payment_requirements

if TYPE_CHECKING:
    from httpx import AsyncClient


class FacilitatorUnavailableError(Exception):
    pass


class FacilitatorClient:
    def __init__(
        self,
        *,
        url: str,
        http_client: AsyncClient | None = None,
    ) -> None:
        self._client = HTTPFacilitatorClient(FacilitatorConfig(url=url, http_client=http_client))

    async def verify(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        try:
            payload = parse_payment_payload(payment_payload)
            requirement = to_payment_requirements(payment_requirement)
            result = await self._client.verify(payload, requirement)
            return result.model_dump(by_alias=True, exclude_none=True)
        except Exception as exc:
            raise FacilitatorUnavailableError("facilitator unavailable") from exc

    async def settle(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        try:
            payload = parse_payment_payload(payment_payload)
            requirement = to_payment_requirements(payment_requirement)
            result = await self._client.settle(payload, requirement)
            return result.model_dump(by_alias=True, exclude_none=True)
        except Exception as exc:
            raise FacilitatorUnavailableError("facilitator unavailable") from exc
