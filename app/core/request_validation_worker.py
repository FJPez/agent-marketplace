"""A request validation worker process: `python -m app.core.request_validation_worker`.

The pool in `app.core.request_validation` starts each worker as a fresh interpreter, with
an empty environment and in a session of its own, and talks to it over its standard input
and output. The worker answers an empty frame once it is ready. For each request (a
schema's canonical JSON and a raw request body) it answers an empty frame once it has read
the request, then the body's refusal, or an empty frame when the body matches. It exits
when its input ends. A request that crashes it (a stack overflow, memory exhaustion) ends
it too, and the pool reports that.
"""

import contextlib
import json
import math
import resource
import struct
import sys
from functools import lru_cache
from typing import BinaryIO

import jsonschema_rs

from app.core.json_types import JsonValue
from app.core.request_schema_validation import compile_request_schema, json_pointer

# A request: the byte lengths of the schema's canonical JSON and of the body that follow.
REQUEST_HEADER = struct.Struct("!II")
# An answer: the byte length of the UTF-8 text that follows.
ANSWER_HEADER = struct.Struct("!I")
# Far deeper than real bodies. It does not prevent every stack overflow (a long `$ref`
# chain applied at each level can still overflow), but those only end the worker.
REQUEST_BODY_MAX_DEPTH = 128
# The most characters of a body's error message, and of its location, a refusal repeats.
REQUEST_BODY_ERROR_TEXT_MAX_LENGTH = 200
VALIDATOR_CACHE_SIZE = 256
# Each worker's address space, enforced on Linux only: macOS does not enforce RLIMIT_AS.
MEMORY_LIMIT_BYTES = 512 * 1024 * 1024

_MISMATCH = "request body does not match the request schema"
_NOT_JSON = "request body is not valid JSON"
_NOT_FINITE = "request body holds a number that is not finite"
_LONE_SURROGATE = "request body holds a string that is not valid Unicode (a lone surrogate)"
_TOO_DEEP = f"request body must nest at most {REQUEST_BODY_MAX_DEPTH} levels"


class _NotFiniteError(ValueError):
    """A number parsed as NaN or an infinity."""


def main() -> None:
    if sys.platform == "linux":
        # A lower limit already set bounds the worker more tightly.
        with contextlib.suppress(ValueError):
            resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    requests, answers = sys.stdin.buffer, sys.stdout.buffer
    _answer(answers, None)  # ready
    while header := requests.read(REQUEST_HEADER.size):
        schema_size, body_size = REQUEST_HEADER.unpack(header)
        schema_json = requests.read(schema_size).decode()
        body = requests.read(body_size)
        _answer(answers, None)  # read
        _answer(answers, body_refusal(schema_json, body))


def body_refusal(schema_json: str, body: bytes) -> str | None:
    """Why the raw JSON `body` is refused against the schema `schema_json`, or None."""
    validator = _compile(schema_json)
    try:
        instance = json.loads(body, parse_constant=_not_finite, parse_float=_finite_float)
    except RecursionError:
        return _TOO_DEEP
    except _NotFiniteError:
        return _NOT_FINITE
    except ValueError:  # malformed, not UTF-8, or an integer of over 4,300 digits
        return _NOT_JSON
    if _nests_deeper_than(instance, REQUEST_BODY_MAX_DEPTH):
        return _TOO_DEEP
    try:
        validator.validate(instance)
    except jsonschema_rs.ValidationError as exc:
        location = json_pointer(exc.instance_path) or "the root"
        return f"{_MISMATCH}: {_cut(exc.message)} at {_cut(location)}"
    except UnicodeEncodeError:
        return _LONE_SURROGATE
    return None


@lru_cache(maxsize=VALIDATOR_CACHE_SIZE)
def _compile(schema_json: str) -> jsonschema_rs.Draft202012Validator:
    return compile_request_schema(json.loads(schema_json))


def _answer(answers: BinaryIO, text: str | None) -> None:
    data = (text or "").encode()
    answers.write(ANSWER_HEADER.pack(len(data)) + data)
    answers.flush()


def _not_finite(name: str) -> float:
    raise _NotFiniteError(name)


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):  # beyond a double's range, such as 1e400
        raise _NotFiniteError(text)
    return value


def _nests_deeper_than(instance: JsonValue, depth: int) -> bool:
    containers = [instance] if isinstance(instance, dict | list) else []
    for _ in range(depth):
        containers = [
            member
            for container in containers
            for member in (container.values() if isinstance(container, dict) else container)
            if isinstance(member, dict | list)
        ]
    return bool(containers)


def _cut(text: str) -> str:
    if len(text) <= REQUEST_BODY_ERROR_TEXT_MAX_LENGTH:
        return text
    return f"{text[:REQUEST_BODY_ERROR_TEXT_MAX_LENGTH]}..."


if __name__ == "__main__":
    main()
