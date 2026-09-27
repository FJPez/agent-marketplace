import json
import re

import pytest

from app.core.json_types import JsonObject
from app.core.request_validation_worker import (
    REQUEST_BODY_ERROR_TEXT_MAX_LENGTH,
    REQUEST_BODY_MAX_DEPTH,
    body_refusal,
)

MISMATCH = "request body does not match the request schema"
NOT_JSON = "request body is not valid JSON"
NOT_FINITE = "request body holds a number that is not finite"
TOO_DEEP = f"request body must nest at most {REQUEST_BODY_MAX_DEPTH} levels"
CUT = REQUEST_BODY_ERROR_TEXT_MAX_LENGTH


def _canonical(schema: JsonObject) -> str:
    return json.dumps(schema, separators=(",", ":"), sort_keys=True)


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
        pytest.param({}, b'"\xff"', NOT_JSON, id="not_utf_8"),
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
