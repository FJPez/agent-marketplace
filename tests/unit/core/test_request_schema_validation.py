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
    check_request_schema,
    check_request_schema_shape,
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
SUBSCHEMA_BUDGET = (
    f"request_schema applies more than {REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE} subschemas "
    "to one value of a request body"
)
PATTERN_BUDGET = "request_schema matches one string of a request body against more than 1 pattern"
NUMBER_BUDGET = (
    "request_schema compares one value of a request body with more than "
    f"{REQUEST_SCHEMA_MAX_NUMBERS_PER_VALUE} numbers"
)
MISMATCH = "request body does not match the request schema"


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


def _fan_out(levels: int, *, container: str = "$defs") -> JsonObject:
    """Each definition applies the previous one twice, so `text` doubles per level."""
    definitions: JsonObject = {"a0": {"type": "string"}}
    for level in range(1, levels):
        previous = {"$ref": f"#/{container}/a{level - 1}"}
        definitions[f"a{level}"] = {"allOf": [previous, previous]}
    return {
        container: definitions,
        "properties": {"text": {"$ref": f"#/{container}/a{levels - 1}"}},
    }


def _items_chain(levels: int) -> JsonObject:
    """A chain of `levels` definitions, each the items of the one before."""
    definitions: JsonObject = {
        str(level): {"items": {"$ref": f"#/$defs/{level + 1}"}} for level in range(levels - 1)
    }
    definitions[str(levels - 1)] = {}
    return {"$defs": definitions, "$ref": "#/$defs/0"}


def _combinations(steps: int) -> JsonObject:
    """A schema that applies about 2**steps different sets of subschemas to body values.

    The subschemas applied to a value record which of the last `steps` keys were "a",
    so none of those sets is large, but there are many of them.
    """
    definitions: JsonObject = {f"s{steps}": {}}
    for step in range(steps - 1, 0, -1):
        after = {"$ref": f"#/$defs/s{step + 1}"}
        definitions[f"s{step}"] = {"properties": {"a": after, "b": after}}
    return {
        "$defs": definitions,
        "properties": {
            "a": {"allOf": [{"$ref": "#"}, {"$ref": "#/$defs/s1"}]},
            "b": {"$ref": "#"},
        },
    }


def _patterns(count: int) -> JsonObject:
    return {"properties": {f"p{index}": {"pattern": "^a"} for index in range(count)}}


def _nested_body(levels: int, *, width: int = 0) -> JsonValue:
    """A request body of `levels` nested arrays, each also holding `width` numbers."""
    numbers: list[JsonValue] = [*range(width)]
    body: JsonValue = numbers
    for _ in range(levels - 1):
        body = [body, *numbers]
    return body


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
            id="nested_objects",
        ),
        pytest.param(
            {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "tags": {"items": {"type": "string"}, "uniqueItems": True},
                    },
                    "required": ["id"],
                },
            },
            id="array_of_objects",
        ),
        pytest.param(
            {
                "$defs": {
                    "address": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
                "properties": {
                    "home": {"$ref": "#/$defs/address"},
                    "work": {"$ref": "#/$defs/address"},
                    "billing": {"allOf": [{"$ref": "#/$defs/address"}, {"required": ["city"]}]},
                },
            },
            id="reused_defs",
        ),
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
            {"properties": {"left": {"$ref": "#"}, "right": {"$ref": "#"}}},
            id="binary_tree",
        ),
        pytest.param(
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
            id="any_json_value",
        ),
        # The longest chain within the size limit: far deeper than any body can be.
        pytest.param(_items_chain(840), id="items_chain_840_deep"),
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
        pytest.param(
            {"if": {"type": "string"}, "then": {"minLength": 1}, "else": {"minimum": 0}},
            id="if_then_else",
        ),
        pytest.param(
            {"prefixItems": [{"pattern": "^a"}], "items": {"pattern": "^b"}},
            id="a_pattern_for_the_prefix_and_one_for_the_rest",
        ),
        pytest.param(
            {
                "items": {
                    "allOf": [{"type": "integer"}] * (REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE - 1)
                }
            },
            id="subschemas_at_budget",
        ),
        pytest.param(
            _nested_all_of(REQUEST_SCHEMA_MAX_APPLICATOR_NESTING), id="applicators_at_limit"
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
        pytest.param(
            {
                "minimum": 0,
                "maximum": 100,
                "exclusiveMinimum": -1,
                "exclusiveMaximum": 101,
                "multipleOf": 1,
            },
            id="integer_bounds_are_not_counted",
        ),
        pytest.param(
            {"enum": list(range(REQUEST_SCHEMA_MAX_NUMBERS_PER_VALUE))},
            id="numbers_at_budget",
        ),
        pytest.param(
            {"const": [None] * REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE},
            id="entries_at_budget",
        ),
        pytest.param({"enum": [f"code-{index}" for index in range(1000)]}, id="string_enum"),
        pytest.param(
            {
                "minimum": -9007199254740991,
                "maximum": 9007199254740991,
                "exclusiveMaximum": 1e15,
                "multipleOf": 1e-05,
                "default": 0.0,
            },
            id="numbers_at_the_bounds",
        ),
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
            {"allOf": [{"$ref": "#"}]},
            "request_schema refers back to a subschema already applied to the same value "
            "at /allOf/0/$ref",
            id="ref_loop_on_one_value",
        ),
        pytest.param(
            {"dependencies": {"a": {"$ref": "#"}}},
            "request_schema refers back to a subschema already applied to the same value "
            "at /dependencies/a/$ref",
            id="legacy_dependencies_apply_to_the_same_value",
        ),
        pytest.param(
            {"$defs": {"x": {"$id": "urn:x", "type": "string"}}, "$ref": "#/$defs/x"},
            "request_schema may declare $id only at its root at /$defs/x/$id",
            id="nested_id",
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
            {"properties": {"code": {"allOf": [{"pattern": "^a"}, {"pattern": "b$"}]}}},
            f"{PATTERN_BUDGET} (at /code)",
            id="two_patterns_on_one_string",
        ),
        pytest.param(
            {"allOf": [{"not": {"pattern": "^a"}}, {"pattern": "b$"}]},
            f"{PATTERN_BUDGET} (at the root)",
            id="a_pattern_under_not_counts",
        ),
        pytest.param(
            {"patternProperties": {"^a": {}, "^b": {}}},
            f"{PATTERN_BUDGET} (at the keys of the root)",
            id="two_patterns_on_one_key",
        ),
        pytest.param(
            {"propertyNames": {"pattern": "^a"}, "patternProperties": {"^b": {}}},
            f"{PATTERN_BUDGET} (at the keys of the root)",
            id="property_names_and_pattern_properties_on_one_key",
        ),
        pytest.param(
            {"prefixItems": [{"pattern": "^a"}], "contains": {"pattern": "^b"}},
            f"{PATTERN_BUDGET} (at /0)",
            id="prefix_item_and_contains",
        ),
        pytest.param(
            {"items": {"pattern": "^a"}, "contains": {"pattern": "^b"}},
            f"{PATTERN_BUDGET} (at /{{any item}})",
            id="items_and_contains",
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
            {"properties": {"a": {"unevaluatedProperties": False}}},
            "request_schema does not support unevaluatedProperties; use additionalProperties "
            "at /properties/a/unevaluatedProperties",
            id="unevaluated_properties",
        ),
        pytest.param(
            {"unevaluatedItems": False},
            "request_schema does not support unevaluatedItems; use items at /unevaluatedItems",
            id="unevaluated_items",
        ),
        pytest.param(
            _nested_all_of(REQUEST_SCHEMA_MAX_APPLICATOR_NESTING + 1),
            "request_schema nests allOf, anyOf, oneOf, not, if, then, else and "
            f"dependentSchemas more than {REQUEST_SCHEMA_MAX_APPLICATOR_NESTING} deep on one "
            "value at the root",
            id="applicators_too_deep",
        ),
        pytest.param(_fan_out(28), f"{SUBSCHEMA_BUDGET} (at /text)", id="ref_fan_out"),
        pytest.param(
            _fan_out(28, container="definitions"),
            f"{SUBSCHEMA_BUDGET} (at /text)",
            id="definitions_fan_out",
        ),
        pytest.param(
            {"type": "object", "properties": {"c": {"allOf": [{"$ref": "#"}, {"$ref": "#"}]}}},
            f"{SUBSCHEMA_BUDGET} (at /c/c/c/c)",
            id="recursive_fan_out",
        ),
        pytest.param(
            {
                "$defs": {
                    "expression": {"oneOf": [{"$ref": "#/$defs/sum"}, {"$ref": "#/$defs/let"}]},
                    "sum": {"properties": {"left": {"$ref": "#/$defs/expression"}}},
                    "let": {"properties": {"left": {"$ref": "#/$defs/expression"}}},
                },
                "$ref": "#/$defs/expression",
            },
            f"{SUBSCHEMA_BUDGET} (at /left/left/left)",
            id="recursive_property_shared_by_variants",
        ),
        pytest.param(
            {"items": {"allOf": [{"type": "integer"}] * REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE}},
            f"{SUBSCHEMA_BUDGET} (at /{{any item}})",
            id="subschemas_over_budget",
        ),
        pytest.param(
            {"additionalProperties": {"allOf": [True] * REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE}},
            f"{SUBSCHEMA_BUDGET} (at /{{any property}})",
            id="subschemas_over_budget_on_any_property",
        ),
        pytest.param(
            {"enum": list(range(REQUEST_SCHEMA_MAX_NUMBERS_PER_VALUE + 1))},
            f"{NUMBER_BUDGET} (at the root)",
            id="numbers_over_budget",
        ),
        pytest.param(
            {
                "minimum": 0.0,
                "maximum": 1e15,
                "exclusiveMinimum": -1.0,
                "exclusiveMaximum": 3.0,
                "multipleOf": 0.5,
            },
            f"{NUMBER_BUDGET} (at the root)",
            id="whole_number_decimal_bounds_count",
        ),
        pytest.param(
            {
                "if": {"minimum": 0.5},
                "then": {"maximum": 1.5},
                "else": {"multipleOf": 0.5, "minimum": 0.1, "maximum": 0.9},
            },
            f"{NUMBER_BUDGET} (at the root)",
            id="if_then_else_numbers",
        ),
        pytest.param(
            {"const": [None] * (REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE + 1)},
            "request_schema compares one value of a request body with more than "
            f"{REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE} enum, const or dependency entries "
            "(at the root)",
            id="entries_over_budget",
        ),
        pytest.param(
            {"dependentRequired": {f"k{index}": [] for index in range(257)}},
            "request_schema compares one value of a request body with more than "
            f"{REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE} enum, const or dependency entries "
            "(at the root)",
            id="dependent_required_entries_over_budget",
        ),
        pytest.param(
            _combinations(13),
            "request_schema combines its subschemas in too many ways to bound the cost of "
            "validating a request body",
            id="too_many_combinations",
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
            {"multipleOf": 1e-300},
            f"request_schema number 1e-300 {NUMBER_RULE} at /multipleOf",
            id="tiny_number",
        ),
        pytest.param(
            {"maximum": 2**53},
            f"request_schema number 9007199254740992 {NUMBER_RULE} at /maximum",
            id="unsafe_integer",
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
        pytest.param(check_request_schema, id="save_path"),
        pytest.param(request_validator, id="invoke_path"),
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
        with pytest.raises(ValueError, match='must be a "#" fragment'):
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
    ("schema", "body"),
    [
        pytest.param(
            {"type": "array", "items": {"type": "number"}},
            [3.2e-7, -1.5e-12, 0.123456789, 12345.678901234567, 1e300, -2.5] * 256,
            id="embedding_of_1536_floats",
        ),
        pytest.param({}, {"document": "Lorem ipsum. " * 8_000}, id="document_of_100_kb"),
        pytest.param(
            {},
            [
                {"id": index, "name": f"item-{index}", "score": 0.5, "tags": ["a", "b"]}
                for index in range(300)
            ],
            id="batch_of_300_objects",
        ),
        pytest.param({}, {"id": 2**64 - 1, "amount": 2**256}, id="uint64_and_uint256"),
        pytest.param({}, [0] * (REQUEST_BODY_MAX_VALUES - 1), id="most_values"),
        pytest.param({}, _nested_body(REQUEST_BODY_MAX_DEPTH), id="deepest"),
        pytest.param(
            {"items": {"minimum": 0}},
            [9007199254740991, 1e-05, 1e15, 0.0, 0],
            id="compared_numbers_at_the_bounds",
        ),
        pytest.param(
            {"properties": {"s": {"pattern": "^a"}}},
            {"s": "a" + "é" * ((REQUEST_BODY_MAX_TEXT_BYTES - 2) // 2)},
            id="most_text_under_a_pattern",
        ),
        pytest.param(
            {"type": "object", "properties": {"text": {"type": "string"}}},
            {"text": "hi é \U0001f600", "flag": True, "nothing": None},
            id="other_values",
        ),
    ],
)
def test_validate_request_body_accepts_a_matching_body_within_its_schemas_bounds(
    schema: JsonObject,
    body: JsonValue,
) -> None:
    validate_request_body(schema=schema, body=body)


@pytest.mark.parametrize(
    ("schema", "body", "message"),
    [
        pytest.param(
            {},
            [0] * 1_000_000,
            f"request body holds more than {REQUEST_BODY_MAX_VALUES} values",
            id="far_too_many_values",
        ),
        pytest.param(
            {"uniqueItems": True, "items": {"$ref": "#"}},
            _nested_body(100, width=14),
            "request body is too large to validate against the request schema within its budget",
            id="nested_unique_items",
        ),
        pytest.param(
            {},
            _nested_body(REQUEST_BODY_MAX_DEPTH + 1),
            f"request body nests more than {REQUEST_BODY_MAX_DEPTH} levels at "
            + "/0" * REQUEST_BODY_MAX_DEPTH,
            id="too_deep",
        ),
        pytest.param(
            {"properties": {"s": {"pattern": "^a"}}},
            {"s": "a" + "é" * (REQUEST_BODY_MAX_TEXT_BYTES // 2)},
            f"request body holds more than {REQUEST_BODY_MAX_TEXT_BYTES} bytes of text "
            "in its strings and keys",
            id="too_much_text_under_a_pattern",
        ),
        pytest.param(
            {"items": {"maximum": 10}},
            [3.2e-7],
            f"request body number 3.2e-07 {NUMBER_RULE} at /0",
            id="tiny_decimal_compared",
        ),
        pytest.param(
            {"additionalProperties": {"maximum": 10}},
            {"n": -1e16},
            f"request body number -1e+16 {NUMBER_RULE} at /n",
            id="huge_decimal_compared",
        ),
        pytest.param(
            {"enum": [1, 2]},
            2**53,
            f"request body number 9007199254740992 {NUMBER_RULE} at the root",
            id="unsafe_integer_compared",
        ),
        pytest.param(
            {},
            [2**256 + 1],
            f"request body number {2**256 + 1} is out of range (integers must be at most "
            "2^256 in magnitude) at /0",
            id="integer_beyond_uint256",
        ),
        pytest.param(
            {}, {"n": float("nan")}, "request body number NaN is not finite at /n", id="nan"
        ),
        pytest.param(
            {},
            [float("-inf")],
            "request body number -Infinity is not finite at /0",
            id="negative_infinity",
        ),
        pytest.param(
            {},
            {"s": "\ud800"},
            "request body string is not valid Unicode (a lone surrogate) at /s",
            id="lone_surrogate",
        ),
        pytest.param(
            {},
            {"\udfff": 1},
            "request body object key is not valid Unicode (a lone surrogate) at the root",
            id="lone_surrogate_key",
        ),
        pytest.param(
            {"properties": {"a/b": {"items": {"type": "string"}}}},
            {"a/b": ["x", 5]},
            f'{MISMATCH}: 5 is not of type "string" at /a~1b/1',
            id="first_error_located",
        ),
        pytest.param(
            {"properties": {"a": {"anyOf": [{"type": "string"}, {"type": "null"}]}}},
            {"a": 5},
            MISMATCH,
            id="not_located_under_any_of",
        ),
        pytest.param(
            {"properties": {"a": {"pattern": "^x"}}},
            {"a": "y"},
            MISMATCH,
            id="not_located_with_a_pattern",
        ),
    ],
)
def test_validate_request_body_rejects_a_body_naming_the_problem(
    schema: JsonObject,
    body: JsonValue,
    message: str,
) -> None:
    with pytest.raises(InvalidInputError, match=f"^{re.escape(message)}$"):
        validate_request_body(schema=schema, body=body)


def test_a_schema_at_the_per_value_limits_gets_a_tight_value_budget() -> None:
    four_numbers: JsonValue = {
        "minimum": 0.5,
        "maximum": 1e6,
        "exclusiveMinimum": 0.25,
        "exclusiveMaximum": 2e6,
    }
    others: list[JsonValue] = [
        {"type": "number"} for _ in range(REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE - 2)
    ]
    heaviest: JsonObject = {"items": {"allOf": [four_numbers, *others]}}
    body: list[JsonValue] = [0.5 for _ in range(5_000)]
    validate_request_body(schema={"items": {"type": "number"}}, body=body)

    with pytest.raises(
        InvalidInputError, match=r"^request body holds more than (\d+) values$"
    ) as error:
        validate_request_body(schema=heaviest, body=body)

    allowed = re.search(r"\d+", str(error.value))
    assert allowed is not None
    assert int(allowed.group()) < 2_000
