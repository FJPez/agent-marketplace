from jsonschema import ValidationError, validate


class PayloadSchemaMismatchError(ValueError):
    pass


def validate_request_payload(
    *,
    payload: dict[str, object],
    request_schema: dict[str, object],
) -> None:
    try:
        validate(instance=payload, schema=request_schema)
    except ValidationError as exc:
        raise PayloadSchemaMismatchError("request payload does not match endpoint schema") from exc
