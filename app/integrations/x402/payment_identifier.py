from __future__ import annotations

from collections.abc import Mapping

from x402.http import decode_payment_signature_header


class InvalidPaymentPayloadError(Exception):
    pass


def parse_payment_header(header_value: str) -> dict[str, object]:
    try:
        payment_payload = decode_payment_signature_header(header_value)
    except Exception as exc:
        raise InvalidPaymentPayloadError("payment payload is not a valid x402 v2 payload") from exc
    if payment_payload.x402_version != 2:
        raise InvalidPaymentPayloadError("payment payload is not a valid x402 v2 payload")

    return payment_payload.model_dump(by_alias=True, exclude_none=True)


def extract_payment_identifier(payment_payload: Mapping[str, object]) -> str:
    payload = _coerce_mapping(payment_payload.get("payload"))
    authorization = _coerce_mapping(payload.get("authorization"))

    for candidate in (
        authorization.get("nonce"),
        payload.get("transaction"),
        payload.get("signature"),
    ):
        if isinstance(candidate, str) and candidate:
            return candidate

    raise InvalidPaymentPayloadError("payment identifier is missing")


def _coerce_mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items() if isinstance(key, str)}
    return {}
