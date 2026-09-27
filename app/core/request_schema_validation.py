"""Rules for the JSON Schemas providers give their endpoints' request bodies.

A request schema is checked when it is saved, and compiled once per schema content for
the invoke path, which validates every request body against it.
"""

import json
from functools import lru_cache

import jsonschema_rs

from app.core.json_types import JsonObject

# The one dialect providers write and the invoke path validates with.
DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
REQUEST_SCHEMA_MAX_DEPTH = 32
REQUEST_SCHEMA_MAX_BYTES = 32_768


def check_request_schema_depth(value: object) -> object:
    """Reject a request schema nested more than REQUEST_SCHEMA_MAX_DEPTH levels deep.

    Runs before the value is validated as JSON, whose errors multiply with the nesting.
    """
    if _nests_deeper_than(value, REQUEST_SCHEMA_MAX_DEPTH):
        msg = f"request_schema must nest at most {REQUEST_SCHEMA_MAX_DEPTH} levels"
        raise ValueError(msg)
    return value


def check_request_schema(schema: JsonObject) -> JsonObject:
    """Accept `schema` only if the invoke path can validate request bodies with it.

    It must be at most REQUEST_SCHEMA_MAX_BYTES of compact JSON, use draft 2020-12, be
    valid under the draft's meta-schema, resolve every `$ref` inside itself (nothing is
    ever fetched) and use only patterns the linear-time regex engine accepts. Raises
    ValueError naming the first rule broken and where in the schema it is broken.
    """
    canonical_schema = _canonical(schema)
    if len(canonical_schema.encode()) > REQUEST_SCHEMA_MAX_BYTES:
        msg = f"request_schema must be at most {REQUEST_SCHEMA_MAX_BYTES} bytes of compact JSON"
        raise ValueError(msg)
    if schema.get("$schema", DRAFT_2020_12) != DRAFT_2020_12:
        msg = f"request_schema must use JSON Schema draft 2020-12 ($schema {DRAFT_2020_12})"
        raise ValueError(msg)
    try:
        _compile(canonical_schema)
    except jsonschema_rs.ValidationError as exc:
        # A JSON Pointer (RFC 6901) to the offending part of the schema.
        location = "".join(
            "/" + str(part).replace("~", "~0").replace("/", "~1") for part in exc.instance_path
        )
        msg = f"request_schema is not a valid JSON Schema: {exc.message}"
        raise ValueError(f"{msg} at {location}" if location else msg) from None
    return schema


def request_validator(schema: JsonObject) -> jsonschema_rs.Draft202012Validator:
    """The compiled validator of a request schema `check_request_schema` accepted.

    Cached by the schema's content: endpoints with the same schema share one validator,
    and an edited schema is compiled afresh.
    """
    return _compile(_canonical(schema))


def _canonical(schema: JsonObject) -> str:
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@lru_cache(maxsize=1024)
def _compile(canonical_schema: str) -> jsonschema_rs.Draft202012Validator:
    return jsonschema_rs.Draft202012Validator(
        json.loads(canonical_schema),
        # Never fetch a remote $ref: the schema is the provider's input.
        offline=True,
        # Linear-time matching, so no pattern can stall the API on a crafted body.
        pattern_options=jsonschema_rs.RegexOptions(),
    )


def _nests_deeper_than(value: object, levels: int) -> bool:
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return False
    return levels == 0 or any(_nests_deeper_than(child, levels - 1) for child in value)
