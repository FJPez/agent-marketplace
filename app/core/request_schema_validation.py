"""Rules for the JSON Schemas providers give their endpoints' request bodies, and for the
request bodies validated against them.

jsonschema-rs holds the GIL while it validates, so one expensive validation stalls the
whole API process. Its cost is bounded as the product of two bounds, both defined here:

- A schema bound, checked when a schema is saved (`check_request_schema`). For each value
  a request body can hold, the schema applies at most
  REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE subschemas to it, compares it with at most
  REQUEST_SCHEMA_MAX_NUMBERS_PER_VALUE numbers and at most
  REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE other enum, const or dependency entries, and
  matches it (or each of its keys) against at most REQUEST_SCHEMA_MAX_PATTERNS_PER_STRING
  pattern, compiled within PATTERN_SIZE_LIMIT bytes.
- A body bound, checked before each body is validated (`validate_request_body`). A body
  holds at most REQUEST_BODY_MAX_VALUES values nested at most REQUEST_BODY_MAX_DEPTH
  deep, and at most REQUEST_BODY_MAX_TEXT_BYTES of text, which a pattern scans. Its
  numbers, like the schema's, are integers of magnitude at most MAX_SAFE_INTEGER or
  decimals that are 0 or of magnitude DECIMAL_MIN_MAGNITUDE to DECIMAL_MAX_MAGNITUDE:
  jsonschema-rs compares numbers exactly, and outside that range one comparison can cost
  hundreds of microseconds.

So one validation applies at most REQUEST_BODY_MAX_VALUES x
REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE subschemas and scans the body's text with at most
one pattern. At these limits the costliest validation measured takes about 42 ms on an
Apple M2 with jsonschema-rs 0.58 (one pattern scanning a 64 KB string), and 32 subschemas
with 4 fractional numbers on each of 1023 numbers about 23 ms.

The body is validated with `is_valid`, in one pass. jsonschema-rs's `validate` describes
the first error by evaluating a failing pattern again, and anyOf and oneOf branches once
more per level, up to ten times the cost, so a refused body is not described. The
compiled validator is cached per schema content for the invoke path.
"""

import json
import math
from collections.abc import Iterator
from functools import lru_cache
from typing import NamedTuple
from urllib.parse import unquote

import jsonschema_rs

from app.core.errors import InvalidInputError
from app.core.json_types import JsonObject, JsonValue

# The one dialect providers write and the invoke path validates with.
DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
REQUEST_SCHEMA_MAX_DEPTH = 32
REQUEST_SCHEMA_MAX_BYTES = 32_768
REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE = 32
REQUEST_SCHEMA_MAX_NUMBERS_PER_VALUE = 4
REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE = 256
REQUEST_SCHEMA_MAX_PATTERNS_PER_STRING = 1
REQUEST_SCHEMA_MAX_PATTERNS = 64
REQUEST_SCHEMA_MAX_APPLICATOR_NESTING = 8
# The check explores the sets of subschemas a body's values can meet; a schema whose
# subschemas combine in more ways than this is refused rather than explored at length.
REQUEST_SCHEMA_MAX_ANALYSIS_STEPS = 10_000
# Regex limits: the compiled program, and each pattern's lazy DFA cache.
PATTERN_SIZE_LIMIT = 10 * 1024
PATTERN_DFA_SIZE_LIMIT = 64 * 1024
REQUEST_BODY_MAX_VALUES = 1024
REQUEST_BODY_MAX_DEPTH = 64
REQUEST_BODY_MAX_TEXT_BYTES = 65_536
MAX_SAFE_INTEGER = 2**53 - 1
DECIMAL_MIN_MAGNITUDE = 1e-05
DECIMAL_MAX_MAGNITUDE = 1e15

# A location in a schema or a body: its JSON Pointer's reference tokens.
type Pointer = tuple[str, ...]

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
_SUBSCHEMA_MAPS = (
    "$defs",
    "definitions",
    "dependencies",
    "dependentSchemas",
    "patternProperties",
    "properties",
)
# Keywords applying their subschemas to the value itself. `dependencies` is the old
# spelling of dependentSchemas and dependentRequired, which jsonschema-rs still applies.
_APPLICATORS = (
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "then",
    "else",
    "dependentSchemas",
    "dependencies",
)
_NUMBER_KEYWORDS = ("exclusiveMaximum", "exclusiveMinimum", "maximum", "minimum", "multipleOf")
_DEPENDENCY_KEYWORDS = ("dependencies", "dependentRequired", "dependentSchemas")
_NUMBER_RANGE = (
    f"is out of range (integers must be at most {MAX_SAFE_INTEGER} in magnitude, "
    f"decimals 0 or {DECIMAL_MIN_MAGNITUDE:g} to {DECIMAL_MAX_MAGNITUDE:g} in magnitude)"
)
_PATTERN_RULE = (
    "is not supported: patterns must avoid lookaround and backreferences "
    f"and compile within {PATTERN_SIZE_LIMIT} bytes"
)


class _Subschema(NamedTuple):
    """Where one subschema leads, and what it compares the value it applies to with.

    `same_value` holds, for each subschema it applies to the same value, that subschema,
    the keyword applying it and whether the keyword is an applicator (not a reference).
    The other fields lead to the value's members.
    """

    same_value: tuple[tuple[Pointer, Pointer, bool], ...]
    properties: dict[str, Pointer]
    pattern_properties: tuple[Pointer, ...]
    additional_properties: Pointer | None
    property_names: Pointer | None
    prefix_items: tuple[Pointer, ...]
    items: Pointer | None
    contains: Pointer | None
    patterns: int
    numbers: int
    entries: int


class _OverBudgetError(Exception):
    """More than REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE subschemas apply to one value."""


def check_request_schema_depth(value: object) -> object:
    """Reject a request schema nested more than REQUEST_SCHEMA_MAX_DEPTH levels deep.

    Runs before the value is validated as JSON, whose errors multiply with the nesting.
    """
    if _nests_deeper_than(value, REQUEST_SCHEMA_MAX_DEPTH):
        msg = f"request_schema must nest at most {REQUEST_SCHEMA_MAX_DEPTH} levels"
        raise ValueError(msg)
    return value


def check_request_schema(schema: JsonObject) -> JsonObject:
    """Accept `schema` only if the invoke path can validate request bodies with it cheaply.

    It must hold only valid Unicode and bounded numbers, take at most
    REQUEST_SCHEMA_MAX_BYTES of compact JSON, use draft 2020-12 throughout, refer only to
    its own subschemas, keep within the schema bound of the module docstring, and be valid
    under the draft's meta-schema. Raises ValueError naming the first rule broken and
    where in the schema, or in a request body it would validate, it is broken.
    """
    for pointer, value in _json_values(schema):
        problem = _value_problem(value)
        if problem:
            msg = f"request_schema {problem} at {_location(pointer)}"
            raise ValueError(msg)
    canonical_schema = _canonical(schema)
    if len(canonical_schema.encode()) > REQUEST_SCHEMA_MAX_BYTES:
        msg = f"request_schema must be at most {REQUEST_SCHEMA_MAX_BYTES} bytes of compact JSON"
        raise ValueError(msg)
    _ValueCosts(_subschemas(schema)).check()
    try:
        _compile(canonical_schema)
    except jsonschema_rs.ValidationError as exc:
        raise ValueError(_schema_error_message(exc)) from None
    return schema


def request_validator(schema: JsonObject) -> jsonschema_rs.Draft202012Validator:
    """The compiled validator of a request schema `check_request_schema` accepted.

    Cached by the schema's content: endpoints with the same schema share one validator,
    and an edited schema is compiled afresh.
    """
    return _compile(_canonical(schema))


def check_request_body_bounds(body: JsonValue) -> None:
    """Reject a request body beyond the body bound of the module docstring.

    Raises InvalidInputError naming the first bound broken and where in the body.
    """
    text_bytes = 0
    for count, (pointer, value) in enumerate(_json_values(body), start=1):
        if count > REQUEST_BODY_MAX_VALUES:
            msg = f"request body holds more than {REQUEST_BODY_MAX_VALUES} values"
            raise InvalidInputError(msg)
        if isinstance(value, dict | list) and len(pointer) >= REQUEST_BODY_MAX_DEPTH:
            msg = (
                f"request body nests more than {REQUEST_BODY_MAX_DEPTH} levels "
                f"at {_location(pointer)}"
            )
            raise InvalidInputError(msg)
        problem = _value_problem(value)
        if problem:
            msg = f"request body {problem} at {_location(pointer)}"
            raise InvalidInputError(msg)
        if isinstance(value, str):
            text_bytes += len(value.encode())
        elif isinstance(value, dict):
            text_bytes += sum(len(key.encode()) for key in value)
        if text_bytes > REQUEST_BODY_MAX_TEXT_BYTES:
            msg = (
                f"request body holds more than {REQUEST_BODY_MAX_TEXT_BYTES} bytes of text "
                "in its strings and keys"
            )
            raise InvalidInputError(msg)


def validate_request_body(*, schema: JsonObject, body: JsonValue) -> None:
    """Validate a request body against its endpoint's accepted request schema.

    Checks the body bound, then validates with the cached compiled validator in one pass.
    Raises InvalidInputError naming the bound broken, or saying the schema refuses the
    body.
    """
    check_request_body_bounds(body)
    if not request_validator(schema).is_valid(body):
        msg = "request body does not match the request schema"
        raise InvalidInputError(msg)


def _canonical(schema: JsonObject) -> str:
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@lru_cache(maxsize=1024)
def _compile(canonical_schema: str) -> jsonschema_rs.Draft202012Validator:
    return jsonschema_rs.Draft202012Validator(
        json.loads(canonical_schema),
        # Never fetch a remote $ref: the schema is the provider's input.
        offline=True,
        # Linear-time matching with bounded programs, so no pattern can stall the API.
        pattern_options=jsonschema_rs.RegexOptions(
            size_limit=PATTERN_SIZE_LIMIT,
            dfa_size_limit=PATTERN_DFA_SIZE_LIMIT,
        ),
    )


def _schema_error_message(exc: jsonschema_rs.ValidationError) -> str:
    path = tuple(str(part) for part in exc.instance_path)
    if exc.kind.as_dict() == {"format": "regex"}:
        # A patternProperties key is the pattern itself; elsewhere the refused value is.
        pattern = path[-1] if path[-2:-1] == ("patternProperties",) else exc.instance
        return f"request_schema pattern {json.dumps(pattern)} {_PATTERN_RULE} at {_pointer(path)}"
    msg = f"request_schema is not a valid JSON Schema: {exc.message}"
    return f"{msg} at {_pointer(path)}" if path else msg


def _subschemas(schema: JsonObject) -> dict[Pointer, _Subschema]:
    """Check the rules local to each subschema of `schema`, and describe where each leads."""
    found = _find_subschemas(schema)
    patterns = 0
    anchors: dict[str, Pointer] = {}
    for pointer, subschema in found.items():
        if not isinstance(subschema, dict):
            continue
        _check_keywords(pointer, subschema)
        patterns += isinstance(subschema.get("pattern"), str)
        pattern_properties = subschema.get("patternProperties")
        patterns += len(pattern_properties) if isinstance(pattern_properties, dict) else 0
        for keyword in ("$anchor", "$dynamicAnchor"):
            name = subschema.get(keyword)
            # jsonschema-rs resolves a repeated anchor to one of its subschemas, and the
            # cost check must follow the same one.
            if isinstance(name, str) and anchors.setdefault(name, pointer) != pointer:
                msg = (
                    f"request_schema declares the anchor {json.dumps(name)} twice "
                    f"at {_pointer((*pointer, keyword))}"
                )
                raise ValueError(msg)
    if patterns > REQUEST_SCHEMA_MAX_PATTERNS:
        msg = f"request_schema must have at most {REQUEST_SCHEMA_MAX_PATTERNS} patterns"
        raise ValueError(msg)
    return {
        # A boolean subschema leads nowhere, like an empty one.
        pointer: _describe(
            pointer, subschema if isinstance(subschema, dict) else {}, found, anchors
        )
        for pointer, subschema in found.items()
    }


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


def _check_keywords(pointer: Pointer, subschema: JsonObject) -> None:
    if subschema.get("$schema", DRAFT_2020_12) != DRAFT_2020_12:
        msg = (
            f"request_schema must use JSON Schema draft 2020-12 ($schema {DRAFT_2020_12}) "
            f"at {_pointer((*pointer, '$schema'))}"
        )
        raise ValueError(msg)
    if pointer and "$id" in subschema:
        # An embedded resource would give the `#` references inside it another base.
        msg = f"request_schema may declare $id only at its root at {_pointer((*pointer, '$id'))}"
        raise ValueError(msg)
    # These collect annotations from every branch applied to a value, which makes nested
    # anyOf cost exponential time.
    for keyword, instead in (
        ("unevaluatedProperties", "additionalProperties"),
        ("unevaluatedItems", "items"),
    ):
        if keyword in subschema:
            msg = (
                f"request_schema does not support {keyword}; use {instead} "
                f"at {_pointer((*pointer, keyword))}"
            )
            raise ValueError(msg)


def _describe(
    pointer: Pointer,
    subschema: JsonObject,
    found: dict[Pointer, JsonObject | bool],
    anchors: dict[str, Pointer],
) -> _Subschema:
    children = [child for child, _ in _children(pointer, subschema)]

    def under(keyword: str) -> tuple[Pointer, ...]:
        return tuple(child for child in children if child[len(pointer)] == keyword)

    same_value = [(child, child, True) for child in children if child[len(pointer)] in _APPLICATORS]
    for keyword in ("$ref", "$dynamicRef"):
        reference = subschema.get(keyword)
        if isinstance(reference, str):
            target = _resolve(reference, found, anchors)
            if target is None:
                msg = (
                    f'request_schema {keyword} {json.dumps(reference)} must be a "#" fragment '
                    f"naming a subschema of this schema at {_pointer((*pointer, keyword))}"
                )
                raise ValueError(msg)
            same_value.append((target, (*pointer, keyword), False))
    compared = [subschema["const"]] if "const" in subschema else []
    enum = subschema.get("enum")
    if isinstance(enum, list):
        # jsonschema-rs finds a value in an enum of strings by hashing, which costs one
        # entry; otherwise it compares the value with each entry in turn.
        strings_only = all(isinstance(item, str) for item in enum)
        compared.extend(enum[:1] if strings_only else enum)
    scalars = [
        value
        for item in compared
        for _, value in _json_values(item)
        if not isinstance(value, dict | list)
    ]
    compared_numbers = sum(
        isinstance(value, int | float) and not isinstance(value, bool) for value in scalars
    )
    return _Subschema(
        same_value=tuple(same_value),
        properties={child[-1]: child for child in under("properties")},
        pattern_properties=under("patternProperties"),
        additional_properties=next(iter(under("additionalProperties")), None),
        property_names=next(iter(under("propertyNames")), None),
        prefix_items=under("prefixItems"),
        items=next(iter(under("items")), None),
        contains=next(iter(under("contains")), None),
        patterns=int(isinstance(subschema.get("pattern"), str)),
        # A bound that is a whole number compares cheaply; a fraction does not.
        numbers=compared_numbers
        + sum(
            isinstance(bound := subschema.get(keyword), float) and not bound.is_integer()
            for keyword in _NUMBER_KEYWORDS
        ),
        entries=len(scalars)
        - compared_numbers
        + sum(
            len(dependencies)
            for keyword in _DEPENDENCY_KEYWORDS
            if isinstance(dependencies := subschema.get(keyword), dict)
        ),
    )


def _resolve(
    reference: str,
    found: dict[Pointer, JsonObject | bool],
    anchors: dict[str, Pointer],
) -> Pointer | None:
    """The subschema a same-document `#` reference names, or None."""
    if not reference.startswith("#"):
        return None
    fragment = unquote(reference[1:])
    if not fragment:
        return ()
    if not fragment.startswith("/"):
        return anchors.get(fragment)
    pointer = tuple(
        token.replace("~1", "/").replace("~0", "~") for token in fragment[1:].split("/")
    )
    return pointer if pointer in found else None


class _ValueCosts:
    """Checks the schema bound for every value a request body can hold.

    A body value meets the subschemas its parent's subschemas apply to it by property,
    pattern or item, with `*` standing for any other member, and, from each of those, the
    subschemas they apply to the same value through applicators and references. The check
    explores every distinct set of subschemas a value can meet, so a recursive `$ref`
    passes only if those sets stop growing: one that fans out again at each level of a
    body soon exceeds the subschema budget.
    """

    def __init__(self, subschemas: dict[Pointer, _Subschema]) -> None:
        self._subschemas = subschemas
        # Per subschema: the subschemas it applies to its value, itself included, and how
        # deeply applicators nest among them.
        self._applied: dict[Pointer, tuple[Pointer, ...]] = {}
        self._nesting: dict[Pointer, int] = {}

    def check(self) -> None:
        root = self._met([()], location="the root")
        seen = {root}
        pending: list[tuple[Pointer, tuple[Pointer, ...]]] = [((), root)]
        steps = 0
        while pending:
            path, met = pending.pop()
            subschemas = [self._subschemas[pointer] for pointer in met]
            self._check_compared(subschemas, patterns=0, location=_location(path))
            keys_location = f"the keys of {_location(path)}"
            keys_met = self._met(
                [subschema.property_names for subschema in subschemas],
                location=keys_location,
            )
            self._check_compared(
                [self._subschemas[pointer] for pointer in keys_met],
                patterns=sum(len(subschema.pattern_properties) for subschema in subschemas),
                location=keys_location,
            )
            steps += len(met) + len(keys_met)
            for member, pointers in _members(subschemas):
                member_path = (*path, member)
                member_met = self._met(pointers, location=_location(member_path))
                steps += len(member_met)
                if member_met and member_met not in seen:
                    seen.add(member_met)
                    pending.append((member_path, member_met))
            if steps > REQUEST_SCHEMA_MAX_ANALYSIS_STEPS:
                msg = (
                    "request_schema combines its subschemas in too many ways to bound the "
                    "cost of validating a request body"
                )
                raise ValueError(msg)

    def _met(self, pointers: list[Pointer | None], *, location: str) -> tuple[Pointer, ...]:
        """The subschemas a value meets when `pointers` apply to it, in a canonical order."""
        met: list[Pointer] = []
        try:
            for pointer in pointers:
                if pointer is not None:
                    met.extend(self._applied_by(pointer, route=()))
                    if len(met) > REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE:
                        raise _OverBudgetError
        except _OverBudgetError:
            msg = (
                "request_schema applies more than "
                f"{REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE} subschemas to one value of a "
                f"request body (at {location})"
            )
            raise ValueError(msg) from None
        return tuple(sorted(met))

    def _applied_by(self, pointer: Pointer, *, route: tuple[Pointer, ...]) -> tuple[Pointer, ...]:
        """`pointer` and the subschemas it applies to the same value.

        `route` holds the subschemas being expanded above it, all applied to that value.
        """
        if pointer in self._applied:
            return self._applied[pointer]
        route = (*route, pointer)
        if len(route) > REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE:
            raise _OverBudgetError
        applied = [pointer]
        nesting = 0
        for target, keyword, through_applicator in self._subschemas[pointer].same_value:
            if target in route:
                msg = (
                    "request_schema refers back to a subschema already applied to the same "
                    f"value at {_pointer(keyword)}"
                )
                raise ValueError(msg)
            applied.extend(self._applied_by(target, route=route))
            if len(applied) > REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE:
                raise _OverBudgetError
            nesting = max(nesting, self._nesting[target] + through_applicator)
        if nesting > REQUEST_SCHEMA_MAX_APPLICATOR_NESTING:
            msg = (
                "request_schema nests allOf, anyOf, oneOf, not, if, then, else and "
                f"dependentSchemas more than {REQUEST_SCHEMA_MAX_APPLICATOR_NESTING} deep on "
                f"one value at {_location(pointer)}"
            )
            raise ValueError(msg)
        self._applied[pointer] = tuple(applied)
        self._nesting[pointer] = nesting
        return self._applied[pointer]

    @staticmethod
    def _check_compared(subschemas: list[_Subschema], *, patterns: int, location: str) -> None:
        """Check what one value is compared with: patterns, numbers and other entries."""
        for count, limit, compared in (
            (
                patterns + sum(subschema.patterns for subschema in subschemas),
                REQUEST_SCHEMA_MAX_PATTERNS_PER_STRING,
                "matches one string of a request body against more than "
                f"{REQUEST_SCHEMA_MAX_PATTERNS_PER_STRING} pattern",
            ),
            (
                sum(subschema.numbers for subschema in subschemas),
                REQUEST_SCHEMA_MAX_NUMBERS_PER_VALUE,
                "compares one value of a request body with more than "
                f"{REQUEST_SCHEMA_MAX_NUMBERS_PER_VALUE} numbers",
            ),
            (
                sum(subschema.entries for subschema in subschemas),
                REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE,
                "compares one value of a request body with more than "
                f"{REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE} enum, const or dependency entries",
            ),
        ):
            if count > limit:
                msg = f"request_schema {compared} (at {location})"
                raise ValueError(msg)


def _members(subschemas: list[_Subschema]) -> Iterator[tuple[str, list[Pointer | None]]]:
    """Each kind of member a value can have, with the subschemas `subschemas` apply to it.

    Every pattern is assumed to match every key, and additionalProperties to apply beside
    them, so no set is smaller than the one jsonschema-rs applies.
    """
    patterned = [pointer for subschema in subschemas for pointer in subschema.pattern_properties]
    names = sorted({name for subschema in subschemas for name in subschema.properties})
    for name in names:
        named = [
            subschema.properties.get(name, subschema.additional_properties)
            for subschema in subschemas
        ]
        yield name, [*named, *patterned]
    yield "*", [*(subschema.additional_properties for subschema in subschemas), *patterned]
    contained = [subschema.contains for subschema in subschemas]
    for index in range(max((len(subschema.prefix_items) for subschema in subschemas), default=0)):
        indexed = [
            subschema.prefix_items[index]
            if index < len(subschema.prefix_items)
            else subschema.items
            for subschema in subschemas
        ]
        yield str(index), [*indexed, *contained]
    yield "*", [*(subschema.items for subschema in subschemas), *contained]


def _json_values(document: JsonValue) -> Iterator[tuple[Pointer, JsonValue]]:
    """Every value in a JSON document, in document order, with its location."""
    pending: list[tuple[Pointer, JsonValue]] = [((), document)]
    while pending:
        pointer, value = pending.pop()
        yield pointer, value
        if isinstance(value, dict):
            pending.extend(((*pointer, key), member) for key, member in reversed(value.items()))
        elif isinstance(value, list):
            pending.extend(
                ((*pointer, str(index)), member)
                for index, member in reversed(list(enumerate(value)))
            )


def _value_problem(value: JsonValue) -> str | None:
    """What unfits a value for validation: invalid Unicode, or a number out of range."""
    if isinstance(value, str):
        return None if _is_unicode(value) else "string is not valid Unicode (a lone surrogate)"
    if isinstance(value, dict):
        if all(_is_unicode(key) for key in value):
            return None
        return "object key is not valid Unicode (a lone surrogate)"
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return f"number {json.dumps(value)} is not finite"
    if isinstance(value, int):
        in_range = abs(value) <= MAX_SAFE_INTEGER
    else:
        in_range = value == 0 or DECIMAL_MIN_MAGNITUDE <= abs(value) <= DECIMAL_MAX_MAGNITUDE
    return None if in_range else f"number {json.dumps(value)} {_NUMBER_RANGE}"


def _is_unicode(text: str) -> bool:
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return True


def _location(pointer: Pointer) -> str:
    return _pointer(pointer) if pointer else "the root"


def _pointer(pointer: Pointer) -> str:
    """The JSON Pointer (RFC 6901) of a location."""
    return "".join("/" + token.replace("~", "~0").replace("/", "~1") for token in pointer)


def _nests_deeper_than(value: object, levels: int) -> bool:
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return False
    return levels == 0 or any(_nests_deeper_than(child, levels - 1) for child in value)
