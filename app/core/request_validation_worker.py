"""A request validation worker process: `python -m app.core.request_validation_worker`.

The pool in `app.core.request_validation` starts each worker as a fresh interpreter, with
an empty environment and in a session of its own, and talks to it over its standard input
and output. The worker answers an empty frame once it is ready. Each request is a kind, a
schema's canonical JSON and, to validate, a raw request body. The worker answers an empty
frame once it has read the request, then the refusal, or an empty frame: when the schema
compiles (CHECK_SCHEMA) or the body matches it (VALIDATE_BODY). It exits when its input
ends. A request that crashes it (a stack overflow, memory exhaustion) ends it too, and the
pool reports that.

The worker compiles each schema once and keeps VALIDATOR_CACHE_SIZE of them; no other
process holds a compiled schema.
"""

import contextlib
import json
import math
import resource
import struct
import sys
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

import jsonschema_rs

from app.core.json_types import JsonValue
from app.core.request_schema_validation import json_pointer

CHECK_SCHEMA = 0
VALIDATE_BODY = 1
# A request: its kind, then the byte lengths of the schema's canonical JSON and of the body
# that follow.
REQUEST_HEADER = struct.Struct("!BII")
# An answer: its outcome, whether the worker exits after it, then the byte length of the
# UTF-8 text that follows.
ANSWER_HEADER = struct.Struct("!B?I")
# Outcomes. NO_REFUSAL also marks the empty frames that say the worker is ready and has
# read a request. UNCOMPILABLE_SCHEMA answers a body sent with a schema that does not
# compile, which the save check makes a provider-side fault.
NO_REFUSAL = 0
REFUSAL = 1
UNCOMPILABLE_SCHEMA = 2
# Far deeper than real bodies. It does not prevent every stack overflow (a long `$ref`
# chain applied at each level can still overflow), but those only end the worker.
REQUEST_BODY_MAX_DEPTH = 128
# The most characters of a body's error message, and of its location, a refusal repeats.
REQUEST_BODY_ERROR_TEXT_MAX_LENGTH = 200
VALIDATOR_CACHE_SIZE = 256
# Each worker's address space, enforced on Linux only: macOS does not enforce RLIMIT_AS.
MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
# Regex limits: the compiled program, and each pattern's lazy DFA cache. They bound a
# worker's memory per pattern, and keep matching linear.
PATTERN_SIZE_LIMIT = 10 * 1024
PATTERN_DFA_SIZE_LIMIT = 64 * 1024

_MISMATCH = "request body does not match the request schema"
_NOT_JSON = "request body is not valid JSON"
_NOT_FINITE = "request body holds a number that is not finite"
_LONE_SURROGATE = "request body holds a string that is not valid Unicode (a lone surrogate)"
_TOO_DEEP = f"request body must nest at most {REQUEST_BODY_MAX_DEPTH} levels"
_PATTERN_RULE = (
    "is not supported: patterns must avoid lookaround and backreferences "
    f"and compile within {PATTERN_SIZE_LIMIT} bytes"
)


class _NotFiniteError(ValueError):
    """A number parsed as NaN or an infinity."""


def main() -> None:
    """Answer requests until the input ends, or the worker outgrows `sys.argv[1]` bytes."""
    recycle_bytes = int(sys.argv[1])
    if sys.platform == "linux":
        # A lower limit already set bounds the worker more tightly.
        with contextlib.suppress(ValueError):
            resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    requests, answers = sys.stdin.buffer, sys.stdout.buffer
    _answer(answers)  # ready
    while header := requests.read(REQUEST_HEADER.size):
        kind, schema_size, body_size = REQUEST_HEADER.unpack(header)
        schema_json = requests.read(schema_size).decode()
        body = requests.read(body_size)
        _answer(answers)  # read
        outcome, text = _outcome(kind, schema_json, body)
        # Exiting returns the memory of the validators it keeps to the system, which
        # clearing their cache would not; the pool starts a fresh worker in its place.
        exiting = _memory_bytes() > recycle_bytes
        _answer(answers, outcome, text, exiting=exiting)
        if exiting:
            return


def _outcome(kind: int, schema_json: str, body: bytes) -> tuple[int, str]:
    """A request's outcome, and the text of its refusal."""
    schema_problem = schema_refusal(schema_json)
    if kind == CHECK_SCHEMA:
        return (NO_REFUSAL, "") if schema_problem is None else (REFUSAL, schema_problem)
    if schema_problem is not None:
        return UNCOMPILABLE_SCHEMA, ""
    refusal = body_refusal(schema_json, body)
    return (NO_REFUSAL, "") if refusal is None else (REFUSAL, refusal)


def schema_refusal(schema_json: str) -> str | None:
    """Why the schema `schema_json` does not compile, or None when it does."""
    try:
        _compile(schema_json)
    except jsonschema_rs.ValidationError as exc:
        path = tuple(str(part) for part in exc.instance_path)
        location = _cut(json_pointer(path))
        if exc.kind.as_dict() == {"format": "regex"}:
            # A refused `pattern` is the value itself; a refused patternProperties key is
            # the subschema's key.
            pattern = exc.instance if isinstance(exc.instance, str) else path[-1]
            return (
                f"request_schema pattern {_cut(json.dumps(pattern))} {_PATTERN_RULE} at {location}"
            )
        msg = f"request_schema is not a valid JSON Schema: {_cut(exc.message)}"
        return f"{msg} at {location}" if path else msg
    except ValueError as exc:  # such as the compiler's own recursion limit
        return f"request_schema is not a valid JSON Schema: {_cut(str(exc))}"
    return None


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
    return jsonschema_rs.Draft202012Validator(
        json.loads(schema_json),
        # Never fetch a remote $ref: the schema is the provider's input.
        offline=True,
        pattern_options=jsonschema_rs.RegexOptions(
            size_limit=PATTERN_SIZE_LIMIT,
            dfa_size_limit=PATTERN_DFA_SIZE_LIMIT,
        ),
    )


def _answer(
    answers: BinaryIO,
    outcome: int = NO_REFUSAL,
    text: str = "",
    *,
    exiting: bool = False,
) -> None:
    data = text.encode()
    answers.write(ANSWER_HEADER.pack(outcome, exiting, len(data)) + data)
    answers.flush()


def _memory_bytes() -> int:
    """The worker's size: on Linux its address space, which MEMORY_LIMIT_BYTES bounds;
    elsewhere its peak resident size."""
    if sys.platform == "linux":
        pages = int(Path("/proc/self/statm").read_text().split()[0])
        return pages * resource.getpagesize()
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # bytes on macOS


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
