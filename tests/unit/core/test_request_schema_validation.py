import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core.json_types import JsonObject
from app.core.request_schema_validation import (
    REQUEST_SCHEMA_MAX_BYTES,
    REQUEST_SCHEMA_MAX_DEPTH,
    check_request_schema,
    check_request_schema_depth,
    request_validator,
)


def _nested(levels: int) -> JsonObject:
    """A schema whose objects nest `levels` deep: {"items": {"items": ... {}}}."""
    schema: JsonObject = {}
    for _ in range(levels - 1):
        schema = {"items": schema}
    return schema


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
        {"$defs": {"text": {"type": "string"}}, "properties": {"a": {"$ref": "#/$defs/text"}}},
        {"$defs": {"text": {"$anchor": "text", "type": "string"}}, "items": {"$ref": "#text"}},
        {"type": "string", "pattern": "^[a-z]+(-[a-z]+)*$"},
        {"properties": {"anything": True, "nothing": False}},
        _nested(REQUEST_SCHEMA_MAX_DEPTH),
    ],
    ids=[
        "empty",
        "object",
        "draft_2020_12",
        "local_pointer",
        "local_anchor",
        "pattern",
        "boolean_subschemas",
        "deepest_allowed",
    ],
)
def test_a_valid_draft_2020_12_schema_is_accepted_unchanged(schema: JsonObject) -> None:
    assert check_request_schema_depth(schema) is schema
    assert check_request_schema(schema) is schema


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (
            {"properties": {"a": {"$ref": "https://schemas.example.com/input.json"}}},
            "request_schema is not a valid JSON Schema: Resource "
            "'https://schemas.example.com/input.json' is not present in a registry and "
            "retrieving it failed: Retrieval is disabled, cannot fetch "
            "https://schemas.example.com/input.json",
        ),
        (
            {"$ref": "#/$defs/missing"},
            "request_schema is not a valid JSON Schema: Pointer '/$defs/missing' does not exist",
        ),
        (
            {"properties": {"a/b": {"type": "objekt"}}},
            'request_schema is not a valid JSON Schema: "objekt" is not valid under any of '
            "the schemas listed in the 'anyOf' keyword at /properties/a~1b/type",
        ),
        (
            {"minLength": -1},
            "request_schema is not a valid JSON Schema: -1 is less than the minimum of 0 "
            "at /minLength",
        ),
        (
            {"type": "string", "pattern": "^(?=.*[0-9]).+$"},
            'request_schema is not a valid JSON Schema: "^(?=.*[0-9]).+$" is not a "regex" '
            "at /pattern",
        ),
        (
            {"patternProperties": {"^(a)\\1$": {}}},
            'request_schema is not a valid JSON Schema: {} is not a "regex" '
            "at /patternProperties/^(a)\\1$",
        ),
        (
            {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"},
            "request_schema must use JSON Schema draft 2020-12 "
            "($schema https://json-schema.org/draft/2020-12/schema)",
        ),
        (
            {"description": "x" * REQUEST_SCHEMA_MAX_BYTES},
            f"request_schema must be at most {REQUEST_SCHEMA_MAX_BYTES} bytes of compact JSON",
        ),
    ],
    ids=[
        "remote_ref",
        "dangling_pointer",
        "invalid_keyword_value",
        "negative_length",
        "lookaround_pattern",
        "backreference_pattern_property",
        "draft_07",
        "too_large",
    ],
)
def test_an_invalid_schema_is_rejected_naming_the_problem(
    schema: JsonObject,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        check_request_schema(schema)


def test_a_schema_nested_too_deep_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match=f"^request_schema must nest at most {REQUEST_SCHEMA_MAX_DEPTH} levels$",
    ):
        check_request_schema_depth(_nested(REQUEST_SCHEMA_MAX_DEPTH + 1))


def test_a_remote_ref_is_never_fetched() -> None:
    requested_paths: list[str] = []

    class SchemaHandler(BaseHTTPRequestHandler):
        """Serves a valid schema at every path, so a fetch would make the ref resolve."""

        def do_GET(self) -> None:
            requested_paths.append(self.path)
            body = b'{"type": "string"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), SchemaHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(ValueError, match="Retrieval is disabled"):
            check_request_schema({"$ref": f"http://127.0.0.1:{server.server_port}/input.json"})
    finally:
        server.shutdown()
        server.server_close()

    assert requested_paths == []


def test_a_request_validator_is_compiled_once_per_schema_content() -> None:
    schema: JsonObject = {"type": "object", "required": ["text"]}
    same_content: JsonObject = {"required": ["text"], "type": "object"}
    edited: JsonObject = {"type": "object", "required": ["text", "lang"]}

    validator = request_validator(schema)

    assert request_validator(same_content) is validator
    assert request_validator(edited) is not validator
    assert validator.is_valid({"text": "hi"})
    assert not request_validator(edited).is_valid({"text": "hi"})


def test_a_catastrophic_backtracking_pattern_is_matched_in_linear_time() -> None:
    validator = request_validator({"type": "string", "pattern": "^(a+)+$"})

    # A backtracking engine takes about 2**n steps on n letters and one mismatch.
    assert not validator.is_valid("a" * 100_000 + "!")
