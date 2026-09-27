import re
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core.json_types import JsonObject
from app.core.request_schema_validation import (
    REQUEST_SCHEMA_MAX_BYTES,
    REQUEST_SCHEMA_MAX_DEPTH,
    REQUEST_SCHEMA_MAX_PATTERNS,
    check_request_schema,
    check_request_schema_shape,
    compile_request_schema,
)

SLUG_PATTERN = "^[a-z0-9-]{1,63}$"
EMAIL_PATTERN = "^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$"
UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
PATTERN_RULE = (
    "is not supported: patterns must avoid lookaround and backreferences "
    "and compile within 10240 bytes"
)


def _nested(levels: int) -> JsonObject:
    """A schema whose objects nest `levels` deep: {"items": {"items": ... {}}}."""
    schema: JsonObject = {}
    for _ in range(levels - 1):
        schema = {"items": schema}
    return schema


def _nested_all_of(levels: int) -> JsonObject:
    """`levels` allOf keywords nested on one value: {"allOf": [{"allOf": [... {}]}]}."""
    schema: JsonObject = {}
    for _ in range(levels):
        schema = {"allOf": [schema]}
    return schema


def _fan_out(levels: int) -> JsonObject:
    """Each definition applies the previous one twice, so `text` doubles per level."""
    definitions: JsonObject = {"a0": {"type": "string"}}
    for level in range(1, levels):
        previous = {"$ref": f"#/$defs/a{level - 1}"}
        definitions[f"a{level}"] = {"allOf": [previous, previous]}
    return {"$defs": definitions, "properties": {"text": {"$ref": f"#/$defs/a{levels - 1}"}}}


def _patterns(count: int) -> JsonObject:
    return {"properties": {f"p{index}": {"pattern": "^a"} for index in range(count)}}


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param({}, id="empty"),
        pytest.param(
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            id="object",
        ),
        pytest.param(
            {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
            id="draft_2020_12",
        ),
        pytest.param(
            {"$schema": "https://json-schema.org/draft/2020-12/schema#", "type": "object"},
            id="draft_2020_12_with_empty_fragment",
        ),
        pytest.param(
            {"$defs": {"text": {"type": "string"}}, "properties": {"a": {"$ref": "#/$defs/text"}}},
            id="local_pointer",
        ),
        pytest.param(
            {"$defs": {"text": {"$anchor": "text", "type": "string"}}, "items": {"$ref": "#text"}},
            id="local_anchor",
        ),
        pytest.param({"type": "string", "pattern": "^[a-z]+(-[a-z]+)*$"}, id="pattern"),
        pytest.param({"properties": {"anything": True, "nothing": False}}, id="boolean_subschemas"),
        pytest.param(_nested(REQUEST_SCHEMA_MAX_DEPTH), id="deepest_allowed"),
        pytest.param(
            {
                "$defs": {
                    "node": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                            "children": {"type": "array", "items": {"$ref": "#/$defs/node"}},
                        },
                        "required": ["value"],
                    },
                },
                "$ref": "#/$defs/node",
            },
            id="tree",
        ),
        pytest.param(
            {"type": "object", "properties": {"value": {}, "next": {"$ref": "#"}}},
            id="linked_list",
        ),
        pytest.param(
            {
                "$id": "https://schemas.example.com/input.json",
                "$defs": {"text": {"type": "string"}},
                "properties": {"a": {"$ref": "#/$defs/text"}},
            },
            id="root_id",
        ),
        pytest.param(
            {
                "$defs": {
                    "node": {
                        "$dynamicAnchor": "node",
                        "properties": {"next": {"$dynamicRef": "#node"}},
                    },
                },
                "$ref": "#/$defs/node",
            },
            id="dynamic_ref",
        ),
        pytest.param(
            {
                "definitions": {"text": {"type": "string"}},
                "dependencies": {
                    "a": ["b"],
                    "c": {"properties": {"d": {"$ref": "#/definitions/text"}}},
                },
            },
            id="legacy_dependencies_and_definitions",
        ),
        pytest.param(_patterns(REQUEST_SCHEMA_MAX_PATTERNS), id="patterns_at_limit"),
        pytest.param(
            {
                "properties": {
                    "slug": {"pattern": SLUG_PATTERN},
                    "email": {"pattern": EMAIL_PATTERN},
                    "id": {"pattern": UUID_PATTERN},
                },
            },
            id="ordinary_patterns",
        ),
        pytest.param(
            {"type": "number", "minimum": 0.5, "maximum": 99.5, "multipleOf": 0.01},
            id="prices",
        ),
        pytest.param({"enum": [f"code-{index}" for index in range(1000)]}, id="string_enum"),
        pytest.param(
            {
                "oneOf": [
                    {"properties": {"id": {"pattern": UUID_PATTERN}, "kind": {"const": kind}}}
                    for kind in ("created", "deleted")
                ],
            },
            id="variants_sharing_a_uuid_pattern",
        ),
        pytest.param(
            {"oneOf": [{"properties": {"version": {"const": version}}} for version in range(5)]},
            id="variants_tagged_by_integer_const",
        ),
        pytest.param(
            {"patternProperties": {"^x-": {}, "^[a-z]+$": {"type": "string"}}},
            id="two_pattern_properties",
        ),
        pytest.param({"type": "number", "multipleOf": 0.000001}, id="six_decimal_amounts"),
        pytest.param({"type": "integer", "maximum": 2**64 - 1}, id="uint64_bound"),
        pytest.param(
            {"properties": {"a": {}}, "unevaluatedProperties": False},
            id="unevaluated_properties",
        ),
        pytest.param(_nested_all_of(12), id="deeply_nested_applicators"),
        # Costly to validate, but bounded by the validation workers' deadline.
        pytest.param(_fan_out(28), id="ref_fan_out"),
    ],
)
def test_a_valid_draft_2020_12_schema_is_accepted_unchanged(schema: JsonObject) -> None:
    assert check_request_schema_shape(schema) is schema
    assert check_request_schema(schema) is schema


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        pytest.param(
            {"properties": {"a": {"$ref": "https://schemas.example.com/input.json"}}},
            'request_schema $ref "https://schemas.example.com/input.json" must be a "#" '
            "fragment naming a subschema of this schema at /properties/a/$ref",
            id="remote_ref",
        ),
        pytest.param(
            {"$ref": "#/$defs/missing"},
            'request_schema $ref "#/$defs/missing" must be a "#" fragment naming a subschema '
            "of this schema at /$ref",
            id="dangling_pointer",
        ),
        pytest.param(
            {"items": {"$ref": "#missing"}},
            'request_schema $ref "#missing" must be a "#" fragment naming a subschema '
            "of this schema at /items/$ref",
            id="dangling_anchor",
        ),
        pytest.param(
            {"properties": {"a": {"type": "string"}}, "$ref": "#/properties"},
            'request_schema $ref "#/properties" must be a "#" fragment naming a subschema '
            "of this schema at /$ref",
            id="ref_to_a_non_subschema",
        ),
        pytest.param(
            {"items": {"$dynamicRef": "https://schemas.example.com/input.json#node"}},
            'request_schema $dynamicRef "https://schemas.example.com/input.json#node" must be '
            'a "#" fragment naming a subschema of this schema at /items/$dynamicRef',
            id="remote_dynamic_ref",
        ),
        pytest.param(
            {"$defs": {"x": {"$id": "urn:x", "type": "string"}}, "$ref": "#/$defs/x"},
            "request_schema may declare $id only at its root at /$defs/x/$id",
            id="nested_id",
        ),
        pytest.param(
            {
                "$defs": {
                    "a": {"$anchor": "node", "type": "string"},
                    "b": {"$dynamicAnchor": "node", "type": "integer"},
                },
                "$ref": "#node",
            },
            'request_schema declares the anchor "node" twice at /$defs/b/$dynamicAnchor',
            id="repeated_anchor",
        ),
        pytest.param(
            {"properties": {"a/b": {"type": "objekt"}}},
            'request_schema is not a valid JSON Schema: "objekt" is not valid under any of '
            "the schemas listed in the 'anyOf' keyword at /properties/a~1b/type",
            id="invalid_keyword_value",
        ),
        pytest.param(
            {"minLength": -1},
            "request_schema is not a valid JSON Schema: -1 is less than the minimum of 0 "
            "at /minLength",
            id="negative_length",
        ),
        pytest.param(
            {"type": "string", "pattern": "^(?=.*[0-9]).+$"},
            f'request_schema pattern "^(?=.*[0-9]).+$" {PATTERN_RULE} at /pattern',
            id="lookaround_pattern",
        ),
        pytest.param(
            {"patternProperties": {"^(a)\\1$": {}}},
            f'request_schema pattern "^(a)\\\\1$" {PATTERN_RULE} at /patternProperties/^(a)\\1$',
            id="backreference_pattern_property",
        ),
        pytest.param(
            {"properties": {"patternProperties": {"pattern": "(?=a)"}}},
            f'request_schema pattern "(?=a)" {PATTERN_RULE} '
            "at /properties/patternProperties/pattern",
            id="pattern_of_a_property_named_patternProperties",
        ),
        pytest.param(
            {"pattern": "((a{50}){50}){50}x"},
            f'request_schema pattern "((a{{50}}){{50}}){{50}}x" {PATTERN_RULE} at /pattern',
            id="nested_counted_repetition",
        ),
        pytest.param(
            {"pattern": "(a{100}){100}x"},
            f'request_schema pattern "(a{{100}}){{100}}x" {PATTERN_RULE} at /pattern',
            id="counted_repetition",
        ),
        pytest.param(
            {"properties": {"name": {"pattern": "^\\p{L}+$"}}},
            f'request_schema pattern "^\\\\p{{L}}+$" {PATTERN_RULE} at /properties/name/pattern',
            id="unicode_letters_pattern",
        ),
        pytest.param(
            {"pattern": "^[A-Za-z0-9+/]{0,256}$"},
            f'request_schema pattern "^[A-Za-z0-9+/]{{0,256}}$" {PATTERN_RULE} at /pattern',
            id="long_bounded_pattern",
        ),
        pytest.param(
            _patterns(REQUEST_SCHEMA_MAX_PATTERNS + 1),
            f"request_schema must have at most {REQUEST_SCHEMA_MAX_PATTERNS} patterns",
            id="too_many_patterns",
        ),
        pytest.param(
            {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"},
            "request_schema must use JSON Schema draft 2020-12 "
            "($schema https://json-schema.org/draft/2020-12/schema) at /$schema",
            id="draft_07",
        ),
        pytest.param(
            {
                "$defs": {
                    "x": {
                        "$id": "urn:x",
                        "$schema": "http://json-schema.org/draft-07/schema#",
                        "format": "regex",
                    },
                },
                "properties": {"a": {"$ref": "urn:x"}},
            },
            "request_schema must use JSON Schema draft 2020-12 "
            "($schema https://json-schema.org/draft/2020-12/schema) at /$defs/x/$schema",
            id="embedded_draft_07",
        ),
        pytest.param(
            {"description": "\ud800"},
            "request_schema string is not valid Unicode (a lone surrogate) at /description",
            id="lone_surrogate",
        ),
        pytest.param(
            {"properties": {"\udfff": {}}},
            "request_schema object key is not valid Unicode (a lone surrogate) at /properties",
            id="lone_surrogate_key",
        ),
        pytest.param(
            {"\udfff": {}},
            "request_schema object key is not valid Unicode (a lone surrogate) at the root",
            id="lone_surrogate_root_key",
        ),
        pytest.param(
            {"maximum": float("inf")},
            "request_schema number Infinity is not finite at /maximum",
            id="overflowing_number",
        ),
        pytest.param(
            {"const": float("nan")},
            "request_schema number NaN is not finite at /const",
            id="nan_const",
        ),
        pytest.param(
            {"description": "x" * REQUEST_SCHEMA_MAX_BYTES},
            f"request_schema must be at most {REQUEST_SCHEMA_MAX_BYTES} bytes of compact JSON",
            id="too_large",
        ),
        pytest.param(
            {"description": "\ud800" + "x" * REQUEST_SCHEMA_MAX_BYTES},
            f"request_schema must be at most {REQUEST_SCHEMA_MAX_BYTES} bytes of compact JSON",
            id="too_large_is_checked_first",
        ),
    ],
)
def test_an_invalid_schema_is_rejected_naming_the_problem(
    schema: JsonObject,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        check_request_schema(schema)


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        pytest.param(
            _nested(REQUEST_SCHEMA_MAX_DEPTH + 1),
            f"request_schema must nest at most {REQUEST_SCHEMA_MAX_DEPTH} levels",
            id="too_deep",
        ),
        pytest.param(
            {"enum": [0] * 1_000_000},
            f"request_schema must be at most {REQUEST_SCHEMA_MAX_BYTES} bytes of compact JSON",
            id="more_values_than_bytes",
        ),
    ],
)
def test_a_schema_too_deep_or_too_large_is_rejected_before_it_is_read_as_json(
    schema: JsonObject,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        check_request_schema_shape(schema)


@pytest.mark.parametrize(
    "check",
    [
        pytest.param(check_request_schema, id="save_check"),
        pytest.param(compile_request_schema, id="workers_compile"),
    ],
)
def test_a_remote_ref_is_never_fetched(check: Callable[[JsonObject], object]) -> None:
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
        with pytest.raises(ValueError, match=f"http://127.0.0.1:{server.server_port}/input.json"):
            check({"$ref": f"http://127.0.0.1:{server.server_port}/input.json"})
    finally:
        server.shutdown()
        server.server_close()

    assert requested_paths == []


def test_a_catastrophic_backtracking_pattern_is_matched_in_linear_time() -> None:
    validator = compile_request_schema({"type": "string", "pattern": "^(a+)+$"})

    # A backtracking engine takes about 2**n steps on n letters and one mismatch.
    assert not validator.is_valid("a" * 100_000 + "!")
