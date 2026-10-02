import re

import pytest

from app.core.json_types import JsonObject
from app.core.request_schema_validation import (
    REQUEST_SCHEMA_MAX_BYTES,
    REQUEST_SCHEMA_MAX_DEPTH,
    REQUEST_SCHEMA_MAX_PATTERNS,
    check_request_schema,
    check_request_schema_shape,
)

SLUG_PATTERN = "^[a-z0-9-]{1,63}$"
EMAIL_PATTERN = "^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$"
UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


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
        # Costly to compile, which only the workers do, under a deadline.
        pytest.param({"pattern": "\\s" * 10_900}, id="costly_to_compile"),
        # Not compilable, which only the workers find.
        pytest.param({"minLength": -1}, id="negative_length"),
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
