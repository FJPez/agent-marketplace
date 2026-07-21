from collections.abc import Mapping

from jsonschema import SchemaError, ValidationError, validate


class ResponseSchemaMismatchError(ValueError):
    pass


def validate_response_payload(
    *,
    payload: object,
    response_schema: Mapping[str, object],
) -> None:
    try:
        validate(instance=payload, schema=response_schema)
    except ValidationError as exc:
        raise ResponseSchemaMismatchError(
            "upstream response does not match advertised response schema",
        ) from exc
    except SchemaError as exc:
        raise ResponseSchemaMismatchError("advertised response schema is invalid") from exc
