import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core.json_types import JsonObject
from app.core.request_validation_worker import (
    REQUEST_BODY_ERROR_TEXT_MAX_LENGTH,
    REQUEST_BODY_MAX_DEPTH,
    body_refusal,
    schema_refusal,
)

MISMATCH = "request body does not match the request schema"
NOT_JSON = "request body is not valid JSON"
NOT_UTF_8 = "request body is not UTF-8"
NOT_FINITE = "request body holds a number that is not finite"
TOO_DEEP = f"request body must nest at most {REQUEST_BODY_MAX_DEPTH} levels"
CUT = REQUEST_BODY_ERROR_TEXT_MAX_LENGTH
PATTERN_RULE = (
    "is not supported: patterns must avoid lookaround and backreferences "
    "and compile within 10240 bytes"
)


def _nested_items(levels: int) -> JsonObject:
    schema: JsonObject = {}
    for _ in range(levels):
        schema = {"items": schema}
    return schema


def _canonical(schema: JsonObject) -> str:
    return json.dumps(schema, separators=(",", ":"), sort_keys=True)


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param({}, id="empty"),
        pytest.param(
            {
                "properties": {
                    "slug": {"pattern": "^[a-z0-9-]{1,63}$"},
                    "email": {"pattern": "^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$"},
                    "id": {
                        "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
                    },
                },
            },
            id="ordinary_patterns",
        ),
        pytest.param(
            {"type": "number", "minimum": 0.5, "maximum": 99.5, "multipleOf": 0.01},
            id="prices",
        ),
        # A pattern in a subschema nothing applies is never compiled.
        pytest.param({"$defs": {"unused": {"pattern": "(?=a)"}}}, id="unreferenced_pattern"),
    ],
)
def test_a_schema_that_compiles_is_not_refused(schema: JsonObject) -> None:
    assert schema_refusal(_canonical(schema)) is None


@pytest.mark.parametrize(
    ("schema", "refusal"),
    [
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
        # jsonschema-rs raises a plain ValueError, not a ValidationError, for this. The
        # request models refuse any schema over 32 levels long before it reaches a worker.
        pytest.param(
            _nested_items(500),
            "request_schema is not a valid JSON Schema: Recursion limit reached",
            id="past_the_compilers_recursion_limit",
        ),
        # Each part a refusal repeats from the schema is cut, as a body's refusal is.
        pytest.param(
            {"required": "x" * 300},
            f'request_schema is not a valid JSON Schema: "{"x" * (CUT - 1)}... at /required',
            id="long_message_cut",
        ),
        pytest.param(
            {"pattern": "(?=a)" + "b" * 300},
            f'request_schema pattern "(?=a){"b" * (CUT - 6)}... {PATTERN_RULE} at /pattern',
            id="long_pattern_cut",
        ),
        pytest.param(
            {"properties": {"k" * 300: {"minLength": -1}}},
            "request_schema is not a valid JSON Schema: -1 is less than the minimum of 0 "
            f"at /properties/{'k' * (CUT - 12)}...",
            id="long_location_cut",
        ),
    ],
)
def test_a_schema_that_does_not_compile_is_refused_naming_the_problem(
    schema: JsonObject,
    refusal: str,
) -> None:
    assert schema_refusal(_canonical(schema)) == refusal


def test_compiling_never_fetches_a_remote_ref() -> None:
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
    url = f"http://127.0.0.1:{server.server_port}/input.json"
    try:
        refusal = schema_refusal(_canonical({"$ref": url}))
    finally:
        server.shutdown()
        server.server_close()

    assert refusal is not None
    assert url in refusal
    assert requested_paths == []


@pytest.mark.parametrize(
    ("schema", "body"),
    [
        pytest.param(
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            b'{"text": "hello", "extra": [1, 2.5, -1e300, null, true, {"k": "v"}]}',
            id="object",
        ),
        pytest.param(
            {}, b"[" * REQUEST_BODY_MAX_DEPTH + b"]" * REQUEST_BODY_MAX_DEPTH, id="deepest"
        ),
        pytest.param({"type": "integer"}, b"1" * 4300, id="integer_of_4300_digits"),
        # jsonschema-rs reads a string's text, and so meets a lone surrogate, only when
        # a keyword needs it.
        pytest.param({"items": {"type": "string"}}, b'["\\ud800"]', id="unread_lone_surrogate"),
    ],
)
def test_a_body_matching_the_schema_is_not_refused(schema: JsonObject, body: bytes) -> None:
    assert body_refusal(_canonical(schema), body) is None


@pytest.mark.parametrize(
    ("schema", "body", "refusal"),
    [
        pytest.param(
            {"properties": {"a/b": {"items": {"type": "integer"}}}},
            b'{"a/b": [1, "x"]}',
            f'{MISMATCH}: "x" is not of type "integer" at /a~1b/1',
            id="first_error_located",
        ),
        pytest.param(
            {"type": "object"},
            b"5",
            f'{MISMATCH}: 5 is not of type "object" at the root',
            id="at_the_root",
        ),
        pytest.param(
            {"items": {"type": "integer"}},
            json.dumps(["x" * 10_000]).encode(),
            f'{MISMATCH}: "{"x" * (CUT - 1)}... at /0',
            id="long_message_cut",
        ),
        pytest.param(
            {"additionalProperties": {"type": "integer"}},
            json.dumps({"k" * 10_000: "x"}).encode(),
            f'{MISMATCH}: "x" is not of type "integer" at /{"k" * (CUT - 1)}...',
            id="long_location_cut",
        ),
        pytest.param({}, b'{"a": ', NOT_JSON, id="malformed"),
        pytest.param({}, b'"\xff"', NOT_UTF_8, id="not_utf_8"),
        # What the provider receives is these bytes: only UTF-8 without a byte order mark
        # is JSON it must accept (RFC 8259), and parsers disagree on which value of a
        # repeated key wins (I-JSON, RFC 7493).
        pytest.param({}, '{"a": 1}'.encode("utf-16"), NOT_UTF_8, id="utf_16"),
        pytest.param({}, '{"a": 1}'.encode("utf-32"), NOT_UTF_8, id="utf_32"),
        pytest.param(
            {},
            b'\xef\xbb\xbf{"a": 1}',
            "request body must not start with a byte order mark",
            id="utf_8_byte_order_mark",
        ),
        pytest.param(
            {"properties": {"amount": {"maximum": 100}}},
            b'{"amount": 999999, "amount": 5}',
            'request body repeats the key "amount"',
            id="repeated_key",
        ),
        pytest.param(
            {},
            b'{"items": [{"id": 1, "id": 2}]}',
            'request body repeats the key "id"',
            id="repeated_nested_key",
        ),
        pytest.param({}, b"1" * 4301, NOT_JSON, id="integer_of_4301_digits"),
        pytest.param({}, b"[NaN]", NOT_FINITE, id="nan"),
        pytest.param({}, b"[-Infinity]", NOT_FINITE, id="negative_infinity"),
        pytest.param({"minimum": 0}, b"1e400", NOT_FINITE, id="beyond_a_double_under_minimum"),
        pytest.param(
            {"multipleOf": 2}, b"[-1e400]", NOT_FINITE, id="beyond_a_double_under_multiple_of"
        ),
        pytest.param(
            {"items": {"type": "integer"}},
            b'["\\ud800"]',
            "request body holds a string that is not valid Unicode (a lone surrogate)",
            id="lone_surrogate",
        ),
        pytest.param(
            {},
            b"[" * (REQUEST_BODY_MAX_DEPTH + 1) + b"]" * (REQUEST_BODY_MAX_DEPTH + 1),
            TOO_DEEP,
            id="too_deep",
        ),
        pytest.param({}, b"[" * 100_000 + b"]" * 100_000, TOO_DEEP, id="far_too_deep"),
    ],
)
def test_a_body_is_refused_naming_the_problem(
    schema: JsonObject,
    body: bytes,
    refusal: str,
) -> None:
    assert body_refusal(_canonical(schema), body) == refusal


def test_a_catastrophic_backtracking_pattern_is_matched_in_linear_time() -> None:
    body = json.dumps("a" * 100_000 + "!").encode()

    # A backtracking engine takes about 2**n steps on n letters and one mismatch.
    refusal = body_refusal(_canonical({"type": "string", "pattern": "^(a+)+$"}), body)

    assert refusal is not None
    assert re.match(f"^{MISMATCH}: ", refusal)
