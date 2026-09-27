import re
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core.errors import InvalidInputError
from app.core.json_types import JsonObject, JsonValue
from app.core.request_schema_validation import (
    REQUEST_BODY_MAX_DEPTH,
    REQUEST_BODY_MAX_TEXT_BYTES,
    REQUEST_BODY_MAX_VALUES,
    REQUEST_SCHEMA_MAX_APPLICATOR_NESTING,
    REQUEST_SCHEMA_MAX_BYTES,
    REQUEST_SCHEMA_MAX_DEPTH,
    REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE,
    REQUEST_SCHEMA_MAX_NUMBERS_PER_VALUE,
    REQUEST_SCHEMA_MAX_PATTERNS,
    REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE,
    check_request_body_bounds,
    check_request_schema,
    check_request_schema_depth,
    request_validator,
    validate_request_body,
)

SLUG_PATTERN = "^[a-z0-9-]{1,63}$"
EMAIL_PATTERN = "^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$"
UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
PATTERN_RULE = (
    "is not supported: patterns must avoid lookaround and backreferences "
    "and compile within 10240 bytes"
)
NUMBER_RULE = (
    "is out of range (integers must be at most 9007199254740991 in magnitude, "
    "decimals 0 or 1e-05 to 1e+15 in magnitude)"
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
    """Each $defs entry applies the previous one twice, so `text` doubles per level."""
    defs: JsonObject = {"a0": {"type": "string"}}
    for level in range(1, levels):
        previous = {"$ref": f"#/$defs/a{level - 1}"}
        defs[f"a{level}"] = {"allOf": [previous, previous]}
    return {"$defs": defs, "properties": {"text": {"$ref": f"#/$defs/a{levels - 1}"}}}


def _combinations(steps: int) -> JsonObject:
    """A schema that applies about 2**steps different sets of subschemas to body values.

    The subschemas applied to a value record which of the last `steps` keys were "a",
    so none of those sets is large, but there are many of them.
    """
    defs: JsonObject = {f"s{steps}": {}}
    for step in range(steps - 1, 0, -1):
        after = {"$ref": f"#/$defs/s{step + 1}"}
        defs[f"s{step}"] = {"properties": {"a": after, "b": after}}
    return {
        "$defs": defs,
        "properties": {
            "a": {"allOf": [{"$ref": "#"}, {"$ref": "#/$defs/s1"}]},
            "b": {"$ref": "#"},
        },
    }


def _patterns(count: int) -> JsonObject:
    return {"properties": {f"p{index}": {"pattern": "^a"} for index in range(count)}}


def _nested_body(levels: int) -> JsonValue:
    """A request body of `levels` nested arrays: [[... []]]."""
    body: JsonValue = []
    for _ in range(levels - 1):
        body = [body]
    return body


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
        {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "address": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                        },
                    },
                },
            },
        },
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "tags": {"items": {"type": "string"}}},
                "required": ["id"],
            },
        },
        {
            "$defs": {"address": {"type": "object", "properties": {"city": {"type": "string"}}}},
            "properties": {
                "home": {"$ref": "#/$defs/address"},
                "work": {"$ref": "#/$defs/address"},
                "billing": {"allOf": [{"$ref": "#/$defs/address"}, {"required": ["city"]}]},
            },
        },
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
        {"type": "object", "properties": {"value": {}, "next": {"$ref": "#"}}},
        {"properties": {"left": {"$ref": "#"}, "right": {"$ref": "#"}}},
        {
            "$defs": {
                "json": {
                    "anyOf": [
                        {"type": ["null", "boolean", "number", "string"]},
                        {"type": "array", "items": {"$ref": "#/$defs/json"}},
                        {"type": "object", "additionalProperties": {"$ref": "#/$defs/json"}},
                    ],
                },
            },
            "$ref": "#/$defs/json",
        },
        {
            "$id": "https://schemas.example.com/input.json",
            "$defs": {"text": {"type": "string"}},
            "properties": {"a": {"$ref": "#/$defs/text"}},
        },
        {
            "$defs": {
                "node": {
                    "$dynamicAnchor": "node",
                    "properties": {"next": {"$dynamicRef": "#node"}},
                },
            },
            "$ref": "#/$defs/node",
        },
        {"items": {"allOf": [{"type": "integer"}] * (REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE - 1)}},
        _nested_all_of(REQUEST_SCHEMA_MAX_APPLICATOR_NESTING),
        _patterns(REQUEST_SCHEMA_MAX_PATTERNS),
        {
            "properties": {
                "slug": {"pattern": SLUG_PATTERN},
                "email": {"pattern": EMAIL_PATTERN},
                "id": {"pattern": UUID_PATTERN},
            },
        },
        {"type": "number", "minimum": 0.5, "maximum": 99.5, "multipleOf": 0.01},
        {"enum": list(range(REQUEST_SCHEMA_MAX_NUMBERS_PER_VALUE))},
        {"const": [None] * REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE},
        {"enum": [f"code-{index}" for index in range(1000)]},
        {
            "minimum": -9007199254740991,
            "maximum": 9007199254740991,
            "exclusiveMaximum": 1e15,
            "multipleOf": 1e-05,
            "default": 0.0,
        },
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
        "nested_objects",
        "array_of_objects",
        "reused_defs",
        "tree",
        "linked_list",
        "binary_tree",
        "any_json_value",
        "root_id",
        "dynamic_ref",
        "subschemas_at_budget",
        "applicators_at_limit",
        "patterns_at_limit",
        "ordinary_patterns",
        "prices",
        "numbers_at_budget",
        "entries_at_budget",
        "string_enum",
        "numbers_at_the_bounds",
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
            'request_schema $ref "https://schemas.example.com/input.json" must be a "#" '
            "fragment naming a subschema of this schema at /properties/a/$ref",
        ),
        (
            {"$ref": "#/$defs/missing"},
            'request_schema $ref "#/$defs/missing" must be a "#" fragment naming a subschema '
            "of this schema at /$ref",
        ),
        (
            {"properties": {"a": {"type": "string"}}, "$ref": "#/properties"},
            'request_schema $ref "#/properties" must be a "#" fragment naming a subschema '
            "of this schema at /$ref",
        ),
        (
            {"items": {"$dynamicRef": "https://schemas.example.com/input.json#node"}},
            'request_schema $dynamicRef "https://schemas.example.com/input.json#node" must be '
            'a "#" fragment naming a subschema of this schema at /items/$dynamicRef',
        ),
        (
            {"allOf": [{"$ref": "#"}]},
            "request_schema refers back to a subschema already applied to the same value "
            "at /allOf/0/$ref",
        ),
        (
            {"$defs": {"x": {"$id": "urn:x", "type": "string"}}, "$ref": "#/$defs/x"},
            "request_schema may declare $id only at its root at /$defs/x/$id",
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
            f'request_schema pattern "^(?=.*[0-9]).+$" {PATTERN_RULE} at /pattern',
        ),
        (
            {"patternProperties": {"^(a)\\1$": {}}},
            f'request_schema pattern "^(a)\\\\1$" {PATTERN_RULE} at /patternProperties/^(a)\\1$',
        ),
        (
            {"pattern": "((a{50}){50}){50}x"},
            f'request_schema pattern "((a{{50}}){{50}}){{50}}x" {PATTERN_RULE} at /pattern',
        ),
        (
            {"pattern": "(a{100}){100}x"},
            f'request_schema pattern "(a{{100}}){{100}}x" {PATTERN_RULE} at /pattern',
        ),
        (
            {"properties": {"name": {"pattern": "^\\p{L}+$"}}},
            f'request_schema pattern "^\\\\p{{L}}+$" {PATTERN_RULE} at /properties/name/pattern',
        ),
        (
            {"pattern": "^[A-Za-z0-9+/]{0,256}$"},
            f'request_schema pattern "^[A-Za-z0-9+/]{{0,256}}$" {PATTERN_RULE} at /pattern',
        ),
        (
            _patterns(REQUEST_SCHEMA_MAX_PATTERNS + 1),
            f"request_schema must have at most {REQUEST_SCHEMA_MAX_PATTERNS} patterns",
        ),
        (
            {"properties": {"code": {"allOf": [{"pattern": "^a"}, {"pattern": "b$"}]}}},
            "request_schema matches one string of a request body against more than 1 "
            "pattern (at /code)",
        ),
        (
            {"patternProperties": {"^a": {}, "^b": {}}},
            "request_schema matches one string of a request body against more than 1 "
            "pattern (at the keys of the root)",
        ),
        (
            {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"},
            "request_schema must use JSON Schema draft 2020-12 "
            "($schema https://json-schema.org/draft/2020-12/schema) at /$schema",
        ),
        (
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
        ),
        (
            {"properties": {"a": {"unevaluatedProperties": False}}},
            "request_schema does not support unevaluatedProperties; use additionalProperties "
            "at /properties/a/unevaluatedProperties",
        ),
        (
            {"unevaluatedItems": False},
            "request_schema does not support unevaluatedItems; use items at /unevaluatedItems",
        ),
        (
            _nested_all_of(REQUEST_SCHEMA_MAX_APPLICATOR_NESTING + 1),
            "request_schema nests allOf, anyOf, oneOf, not, if, then, else and "
            f"dependentSchemas more than {REQUEST_SCHEMA_MAX_APPLICATOR_NESTING} deep on one "
            "value at the root",
        ),
        (
            _fan_out(28),
            "request_schema applies more than "
            f"{REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE} subschemas to one value of a request "
            "body (at /text)",
        ),
        (
            {"type": "object", "properties": {"c": {"allOf": [{"$ref": "#"}, {"$ref": "#"}]}}},
            "request_schema applies more than "
            f"{REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE} subschemas to one value of a request "
            "body (at /c/c/c/c)",
        ),
        (
            {
                "$defs": {
                    "expression": {"oneOf": [{"$ref": "#/$defs/sum"}, {"$ref": "#/$defs/let"}]},
                    "sum": {"properties": {"left": {"$ref": "#/$defs/expression"}}},
                    "let": {"properties": {"left": {"$ref": "#/$defs/expression"}}},
                },
                "$ref": "#/$defs/expression",
            },
            "request_schema applies more than "
            f"{REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE} subschemas to one value of a request "
            "body (at /left/left/left)",
        ),
        (
            {"items": {"allOf": [{"type": "integer"}] * REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE}},
            "request_schema applies more than "
            f"{REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE} subschemas to one value of a request "
            "body (at /*)",
        ),
        (
            {"enum": list(range(REQUEST_SCHEMA_MAX_NUMBERS_PER_VALUE + 1))},
            "request_schema compares one value of a request body with more than "
            f"{REQUEST_SCHEMA_MAX_NUMBERS_PER_VALUE} numbers (at the root)",
        ),
        (
            {"const": [None] * (REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE + 1)},
            "request_schema compares one value of a request body with more than "
            f"{REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE} enum, const or dependency entries "
            "(at the root)",
        ),
        (
            _combinations(13),
            "request_schema combines its subschemas in too many ways to bound the cost of "
            "validating a request body",
        ),
        (
            {"description": "\ud800"},
            "request_schema string is not valid Unicode (a lone surrogate) at /description",
        ),
        (
            {"properties": {"\udfff": {}}},
            "request_schema object key is not valid Unicode (a lone surrogate) at /properties",
        ),
        (
            {"maximum": float("inf")},
            "request_schema number Infinity is not finite at /maximum",
        ),
        (
            {"const": float("nan")},
            "request_schema number NaN is not finite at /const",
        ),
        (
            {"multipleOf": 1e-300},
            f"request_schema number 1e-300 {NUMBER_RULE} at /multipleOf",
        ),
        (
            {"maximum": 2**53},
            f"request_schema number 9007199254740992 {NUMBER_RULE} at /maximum",
        ),
        (
            {"description": "x" * REQUEST_SCHEMA_MAX_BYTES},
            f"request_schema must be at most {REQUEST_SCHEMA_MAX_BYTES} bytes of compact JSON",
        ),
    ],
    ids=[
        "remote_ref",
        "dangling_pointer",
        "ref_to_a_non_subschema",
        "remote_dynamic_ref",
        "ref_loop_on_one_value",
        "nested_id",
        "invalid_keyword_value",
        "negative_length",
        "lookaround_pattern",
        "backreference_pattern_property",
        "nested_counted_repetition",
        "counted_repetition",
        "unicode_letters_pattern",
        "long_bounded_pattern",
        "too_many_patterns",
        "two_patterns_on_one_string",
        "two_patterns_on_one_key",
        "draft_07",
        "embedded_draft_07",
        "unevaluated_properties",
        "unevaluated_items",
        "applicators_too_deep",
        "ref_fan_out",
        "recursive_fan_out",
        "recursive_property_shared_by_variants",
        "subschemas_over_budget",
        "numbers_over_budget",
        "entries_over_budget",
        "too_many_combinations",
        "lone_surrogate",
        "lone_surrogate_key",
        "overflowing_number",
        "nan_const",
        "tiny_number",
        "unsafe_integer",
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


@pytest.mark.parametrize(
    ("check", "refusal"),
    [
        (check_request_schema, 'must be a "#" fragment'),
        (request_validator, "Retrieval is disabled"),
    ],
    ids=["save_path", "invoke_path"],
)
def test_a_remote_ref_is_never_fetched(
    check: Callable[[JsonObject], object],
    refusal: str,
) -> None:
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
        with pytest.raises(ValueError, match=re.escape(refusal)):
            check({"$ref": f"http://127.0.0.1:{server.server_port}/input.json"})
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


@pytest.mark.parametrize(
    "body",
    [
        [0] * (REQUEST_BODY_MAX_VALUES - 1),
        _nested_body(REQUEST_BODY_MAX_DEPTH),
        {"max": 9007199254740991, "min": -9007199254740991, "zero": 0},
        {"large": 1e15, "small": 1e-05, "negative": -1e-05, "zero": 0.0, "negative_zero": -0.0},
        {"text": "hi é \U0001f600", "flag": True, "nothing": None},
        {"k" * 6: "é" * ((REQUEST_BODY_MAX_TEXT_BYTES - 6) // 2)},
        "a string body",
    ],
    ids=["most_values", "deepest", "integers", "decimals", "other_values", "most_text", "scalar"],
)
def test_a_request_body_within_the_bounds_is_accepted(body: JsonValue) -> None:
    check_request_body_bounds(body)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            [0] * REQUEST_BODY_MAX_VALUES,
            f"request body holds more than {REQUEST_BODY_MAX_VALUES} values",
        ),
        (
            {"k" * 7: "é" * ((REQUEST_BODY_MAX_TEXT_BYTES - 6) // 2)},
            f"request body holds more than {REQUEST_BODY_MAX_TEXT_BYTES} bytes of text "
            "in its strings and keys",
        ),
        (
            _nested_body(REQUEST_BODY_MAX_DEPTH + 1),
            f"request body nests more than {REQUEST_BODY_MAX_DEPTH} levels at "
            + "/0" * REQUEST_BODY_MAX_DEPTH,
        ),
        ({"n": 2**53}, f"request body number 9007199254740992 {NUMBER_RULE} at /n"),
        ({"n": -1e16}, f"request body number -1e+16 {NUMBER_RULE} at /n"),
        ({"n": 1e-06}, f"request body number 1e-06 {NUMBER_RULE} at /n"),
        (2**53, f"request body number 9007199254740992 {NUMBER_RULE} at the root"),
        ({"n": float("nan")}, "request body number NaN is not finite at /n"),
        ([float("-inf")], "request body number -Infinity is not finite at /0"),
        (
            {"s": "\ud800"},
            "request body string is not valid Unicode (a lone surrogate) at /s",
        ),
        (
            {"\udfff": 1},
            "request body object key is not valid Unicode (a lone surrogate) at the root",
        ),
    ],
    ids=[
        "too_many_values",
        "too_much_text",
        "too_deep",
        "unsafe_integer",
        "huge_decimal",
        "tiny_decimal",
        "unsafe_integer_body",
        "nan",
        "negative_infinity",
        "lone_surrogate",
        "lone_surrogate_key",
    ],
)
def test_a_request_body_outside_the_bounds_is_rejected_naming_the_problem(
    body: JsonValue,
    message: str,
) -> None:
    with pytest.raises(InvalidInputError, match=f"^{re.escape(message)}$"):
        check_request_body_bounds(body)


def test_validate_request_body_accepts_a_matching_body() -> None:
    validate_request_body(
        schema={"type": "object", "properties": {"text": {"type": "string"}}},
        body={"text": "hello"},
    )


def test_validate_request_body_rejects_a_body_the_schema_refuses() -> None:
    with pytest.raises(
        InvalidInputError, match=r"^request body does not match the request schema$"
    ):
        validate_request_body(
            schema={"properties": {"a/b": {"items": {"type": "string"}}}},
            body={"a/b": ["x", 5]},
        )


def test_validate_request_body_checks_the_bounds_before_validating() -> None:
    with pytest.raises(InvalidInputError, match=r"^request body number 1e-06 is out of range"):
        validate_request_body(schema={"type": "string"}, body=1e-06)
