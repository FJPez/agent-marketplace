import pytest

from app.core.response_schema_validation import (
    ResponseSchemaMismatchError,
    validate_response_payload,
)


@pytest.mark.parametrize(
    ("payload", "response_schema"),
    [
        ({"result": "ok"}, {"type": "object"}),
        (["ok"], {"type": "array", "items": {"type": "string"}}),
        ("ok", {"type": "string"}),
        (42, {"type": "integer"}),
        (1.5, {"type": "number"}),
        (True, {"type": "boolean"}),
        (None, {"type": "null"}),
    ],
)
def test_validate_response_payload_accepts_all_json_types(
    payload: object,
    response_schema: dict[str, object],
) -> None:
    validate_response_payload(payload=payload, response_schema=response_schema)


def test_validate_response_payload_rejects_schema_mismatch() -> None:
    with pytest.raises(
        ResponseSchemaMismatchError,
        match="upstream response does not match advertised response schema",
    ):
        validate_response_payload(
            payload="not an object",
            response_schema={"type": "object"},
        )


def test_validate_response_payload_rejects_invalid_provider_schema() -> None:
    with pytest.raises(
        ResponseSchemaMismatchError,
        match="advertised response schema is invalid",
    ):
        validate_response_payload(
            payload={"result": "ok"},
            response_schema={"type": "not-a-json-schema-type"},
        )
