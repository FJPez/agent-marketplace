from collections.abc import Mapping

from jsonschema import ValidationError, validate


class PayloadSchemaMismatchError(ValueError):
    pass


def validate_request_payload(
    *,
    payload: object,
    request_schema: Mapping[str, object],
) -> None:
    try:
        validate(instance=payload, schema=request_schema)
    except ValidationError as exc:
        raise PayloadSchemaMismatchError("request payload does not match endpoint schema") from exc
