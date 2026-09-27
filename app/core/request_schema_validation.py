"""Rules for the JSON Schemas providers give their endpoints' request bodies.

A request schema is checked when it is saved (`check_request_schema`). The checks are
cheap and about correctness: the schema's size and nesting, valid Unicode and finite
numbers, draft 2020-12 throughout, references only to its own subschemas (so nothing is
ever fetched), and that it compiles. Its patterns are compiled within PATTERN_SIZE_LIMIT
bytes, at most REQUEST_SCHEMA_MAX_PATTERNS of them, so compiling a schema stays cheap.

What validating a body costs is not bounded here: request bodies are validated in worker
processes under a deadline (`app.core.request_body_validation`), which compile each schema
with `compile_request_schema`, as the save check does.
"""

import json
import math
from collections.abc import Iterable, Iterator
from urllib.parse import unquote

import jsonschema_rs

from app.core.json_types import JsonObject, JsonValue

# The one dialect providers write and the workers validate with.
DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
REQUEST_SCHEMA_MAX_DEPTH = 32
REQUEST_SCHEMA_MAX_BYTES = 32_768
REQUEST_SCHEMA_MAX_PATTERNS = 64
# Regex limits: the compiled program, and each pattern's lazy DFA cache.
PATTERN_SIZE_LIMIT = 10 * 1024
PATTERN_DFA_SIZE_LIMIT = 64 * 1024

# A location in a schema: its JSON Pointer's reference tokens.
type Pointer = tuple[str, ...]
# A location in a schema's JSON: its parent's location and its key or index, or None at
# the root. Linked rather than copied, so that walking costs the same at any depth.
type Where = tuple[Where, str | int] | None

# Keywords holding one subschema, a list of subschemas, or a map of names to subschemas.
_ONE_SUBSCHEMA = (
    "additionalProperties",
    "contains",
    "contentSchema",
    "else",
    "if",
    "items",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
)
_SUBSCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")
# jsonschema-rs still resolves `definitions` and applies `dependencies`, the old spellings.
_SUBSCHEMA_MAPS = (
    "$defs",
    "definitions",
    "dependencies",
    "dependentSchemas",
    "patternProperties",
    "properties",
)
_PATTERN_RULE = (
    "is not supported: patterns must avoid lookaround and backreferences "
    f"and compile within {PATTERN_SIZE_LIMIT} bytes"
)


def check_request_schema_shape(value: JsonValue) -> JsonValue:
    """Reject a request schema nested more than REQUEST_SCHEMA_MAX_DEPTH levels deep, or
    holding more values than can fit REQUEST_SCHEMA_MAX_BYTES.

    Runs before the value is validated as JSON, whose errors multiply with the nesting and
    whose cost grows with the size. Each container's members are counted before they are
    walked, so an oversized value is refused early.
    """
    if not isinstance(value, dict):
        return value  # refused next, as not a JSON object
    values = 1
    for member, depth, _ in _json_values(value):
        if isinstance(member, dict | list):
            if depth >= REQUEST_SCHEMA_MAX_DEPTH:
                msg = f"request_schema must nest at most {REQUEST_SCHEMA_MAX_DEPTH} levels"
                raise ValueError(msg)
            values += len(member)
            # Every value takes at least one byte.
            if values > REQUEST_SCHEMA_MAX_BYTES:
                msg = (
                    f"request_schema must be at most {REQUEST_SCHEMA_MAX_BYTES} bytes of "
                    "compact JSON"
                )
                raise ValueError(msg)
    return value


def check_request_schema(schema: JsonObject) -> JsonObject:
    """Accept `schema` only if it is a sound draft 2020-12 schema the workers can compile.

    It must take at most REQUEST_SCHEMA_MAX_BYTES of compact JSON, hold only valid
    Unicode and finite numbers, use draft 2020-12 throughout, refer only to its own
    subschemas, have at most REQUEST_SCHEMA_MAX_PATTERNS patterns, and compile. Raises
    ValueError naming the first rule broken and where in the schema it is broken.
    """
    compact = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    if len(compact.encode("utf-8", "surrogatepass")) > REQUEST_SCHEMA_MAX_BYTES:
        msg = f"request_schema must be at most {REQUEST_SCHEMA_MAX_BYTES} bytes of compact JSON"
        raise ValueError(msg)
    for value, _, where in _json_values(schema):
        problem = _value_problem(value)
        if problem:
            msg = f"request_schema {problem} at {_where_location(where)}"
            raise ValueError(msg)
    _check_subschemas(schema)
    try:
        compile_request_schema(schema)
    except jsonschema_rs.ValidationError as exc:
        raise ValueError(_schema_error_message(exc)) from None
    return schema


def compile_request_schema(schema: JsonObject) -> jsonschema_rs.Draft202012Validator:
    """Compile a request schema as the save check and the validation workers do.

    Raises jsonschema_rs.ValidationError when `schema` is not a valid JSON Schema.
    """
    return jsonschema_rs.Draft202012Validator(
        schema,
        # Never fetch a remote $ref: the schema is the provider's input.
        offline=True,
        # Linear-time matching with bounded programs.
        pattern_options=jsonschema_rs.RegexOptions(
            size_limit=PATTERN_SIZE_LIMIT,
            dfa_size_limit=PATTERN_DFA_SIZE_LIMIT,
        ),
    )


def json_pointer(tokens: Iterable[str | int]) -> str:
    """The JSON Pointer (RFC 6901) of a location, given its keys and indexes."""
    return "".join("/" + str(token).replace("~", "~0").replace("/", "~1") for token in tokens)


def _schema_error_message(exc: jsonschema_rs.ValidationError) -> str:
    path = tuple(str(part) for part in exc.instance_path)
    if exc.kind.as_dict() == {"format": "regex"}:
        # A refused `pattern` is the value itself; a refused patternProperties key is the
        # subschema's key.
        pattern = exc.instance if isinstance(exc.instance, str) else path[-1]
        return (
            f"request_schema pattern {json.dumps(pattern)} {_PATTERN_RULE} at {json_pointer(path)}"
        )
    msg = f"request_schema is not a valid JSON Schema: {exc.message}"
    return f"{msg} at {json_pointer(path)}" if path else msg


def _check_subschemas(schema: JsonObject) -> None:
    """Check each subschema's dialect, `$id`, anchors and references, and count patterns."""
    found = _find_subschemas(schema)
    subschemas = [
        (pointer, subschema) for pointer, subschema in found.items() if isinstance(subschema, dict)
    ]
    anchors: dict[str, Pointer] = {}
    patterns = 0
    for pointer, subschema in subschemas:
        # The empty fragment names the same meta-schema.
        if subschema.get("$schema", DRAFT_2020_12) not in (DRAFT_2020_12, f"{DRAFT_2020_12}#"):
            msg = (
                f"request_schema must use JSON Schema draft 2020-12 ($schema {DRAFT_2020_12}) "
                f"at {json_pointer((*pointer, '$schema'))}"
            )
            raise ValueError(msg)
        if pointer and "$id" in subschema:
            # An embedded resource would give the `#` references inside it another base.
            msg = (
                "request_schema may declare $id only at its root "
                f"at {json_pointer((*pointer, '$id'))}"
            )
            raise ValueError(msg)
        for keyword in ("$anchor", "$dynamicAnchor"):
            name = subschema.get(keyword)
            # The draft leaves a repeated anchor undefined; jsonschema-rs silently picks one.
            if isinstance(name, str) and anchors.setdefault(name, pointer) != pointer:
                msg = (
                    f"request_schema declares the anchor {json.dumps(name)} twice "
                    f"at {json_pointer((*pointer, keyword))}"
                )
                raise ValueError(msg)
        patterns += isinstance(subschema.get("pattern"), str)
        pattern_properties = subschema.get("patternProperties")
        patterns += len(pattern_properties) if isinstance(pattern_properties, dict) else 0
    if patterns > REQUEST_SCHEMA_MAX_PATTERNS:
        msg = f"request_schema must have at most {REQUEST_SCHEMA_MAX_PATTERNS} patterns"
        raise ValueError(msg)
    for pointer, subschema in subschemas:
        for keyword in ("$ref", "$dynamicRef"):
            reference = subschema.get(keyword)
            if isinstance(reference, str) and not _names_a_subschema(reference, found, anchors):
                msg = (
                    f'request_schema {keyword} {json.dumps(reference)} must be a "#" fragment '
                    f"naming a subschema of this schema at {json_pointer((*pointer, keyword))}"
                )
                raise ValueError(msg)


def _find_subschemas(schema: JsonObject) -> dict[Pointer, JsonObject | bool]:
    """`schema` and all its subschemas, in document order."""
    found: dict[Pointer, JsonObject | bool] = {}
    pending: list[tuple[Pointer, JsonObject | bool]] = [((), schema)]
    while pending:
        pointer, subschema = pending.pop()
        found[pointer] = subschema
        if isinstance(subschema, dict):
            pending.extend(reversed(list(_children(pointer, subschema))))
    return found


def _children(
    pointer: Pointer,
    subschema: JsonObject,
) -> Iterator[tuple[Pointer, JsonObject | bool]]:
    for keyword, value in subschema.items():
        if keyword in _ONE_SUBSCHEMA and isinstance(value, dict | bool):
            yield (*pointer, keyword), value
        elif keyword in _SUBSCHEMA_LISTS and isinstance(value, list):
            for index, child in enumerate(value):
                if isinstance(child, dict | bool):
                    yield (*pointer, keyword, str(index)), child
        elif keyword in _SUBSCHEMA_MAPS and isinstance(value, dict):
            for name, child in value.items():
                if isinstance(child, dict | bool):
                    yield (*pointer, keyword, name), child


def _names_a_subschema(
    reference: str,
    found: dict[Pointer, JsonObject | bool],
    anchors: dict[str, Pointer],
) -> bool:
    """Whether `reference` is a same-document `#` fragment naming one of `found`."""
    if not reference.startswith("#"):
        return False
    fragment = unquote(reference[1:])
    if not fragment.startswith("/"):
        return not fragment or fragment in anchors
    pointer = tuple(
        token.replace("~1", "/").replace("~0", "~") for token in fragment[1:].split("/")
    )
    return pointer in found


def _json_values(document: JsonValue) -> Iterator[tuple[JsonValue, int, Where]]:
    """Every value in a JSON document, in document order, with its depth and location.

    A container's members are queued only when the walk resumes after it, so a caller can
    stop at a container too large to walk.
    """
    pending: list[tuple[JsonValue, int, Where]] = [(document, 0, None)]
    while pending:
        value, depth, where = pending.pop()
        yield value, depth, where
        if isinstance(value, dict):
            pending.extend(
                (member, depth + 1, (where, key)) for key, member in reversed(value.items())
            )
        elif isinstance(value, list):
            pending.extend(
                (value[index], depth + 1, (where, index)) for index in range(len(value) - 1, -1, -1)
            )


def _value_problem(value: JsonValue) -> str | None:
    """What makes a value invalid JSON: invalid Unicode, or a number that is not finite."""
    if isinstance(value, str):
        return None if _is_unicode(value) else "string is not valid Unicode (a lone surrogate)"
    if isinstance(value, dict):
        if all(_is_unicode(key) for key in value):
            return None
        return "object key is not valid Unicode (a lone surrogate)"
    if isinstance(value, float) and not math.isfinite(value):
        return f"number {json.dumps(value)} is not finite"
    return None


def _is_unicode(text: str) -> bool:
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return True


def _where_location(where: Where) -> str:
    tokens: list[str | int] = []
    while where is not None:
        where, token = where
        tokens.append(token)
    return json_pointer(reversed(tokens)) or "the root"
