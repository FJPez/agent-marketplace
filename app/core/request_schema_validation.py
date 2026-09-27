"""Rules for the JSON Schemas providers give their endpoints' request bodies, and for the
request bodies validated against them.

jsonschema-rs holds the GIL while it validates, so one expensive validation stalls the
whole API process. Its cost is bounded by two checks, both defined here:

- When a schema is saved (`check_request_schema`), an analysis explores every set of
  subschemas a body value can meet. Each set may apply at most
  REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE subschemas, compare the value with at most
  REQUEST_SCHEMA_MAX_NUMBERS_PER_VALUE numbers and REQUEST_SCHEMA_MAX_ENTRIES_PER_VALUE
  other entries, and match it (or each of its keys) against at most one pattern,
  compiled within PATTERN_SIZE_LIMIT bytes. The analysis also yields the schema's body
  budget: the worst cost of one body value, from the measured costs below, and whether
  the schema compares numbers, matches patterns or can have its first error located.
- Before a body is validated (`validate_request_body`), it is walked once against that
  budget. The body's values, each at the worst cost of one value plus the uniqueItems
  hashing above it, must fit REQUEST_BODY_BUDGET_NS, and there are at most
  REQUEST_BODY_MAX_VALUES of them, nested at most REQUEST_BODY_MAX_DEPTH deep. Numbers
  must be finite and integers at most REQUEST_BODY_MAX_INTEGER in magnitude; when the
  schema compares numbers, jsonschema-rs compares them exactly, so they must also be
  integers of magnitude at most MAX_SAFE_INTEGER or decimals that are 0 or of magnitude
  DECIMAL_MIN_MAGNITUDE to DECIMAL_MAX_MAGNITUDE. When the schema matches patterns, the
  body holds at most REQUEST_BODY_MAX_TEXT_BYTES of text for its one pattern per string
  to scan.

So validating one body costs at most REQUEST_BODY_BUDGET_NS by the model (its values
fit the budget at the worst cost of one value each), plus one pattern scan over at most
REQUEST_BODY_MAX_TEXT_BYTES, which costs up to about 67 ms once the matcher is in its slow
state. The costliest schema and body measured together take about 89 ms on an Apple M2.

A body is validated in one pass with `is_valid`, unless the schema reaches no anyOf,
oneOf or pattern: jsonschema-rs's `validate` describes the first error by evaluating those
again, and elsewhere costs no more, so then it names the error.
"""

import json
import math
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
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
# The analysis explores the sets of subschemas a body's values can meet; a schema whose
# subschemas combine in more ways than this is refused rather than explored at length.
REQUEST_SCHEMA_MAX_ANALYSIS_STEPS = 10_000
# Regex limits: the compiled program, and each pattern's lazy DFA cache.
PATTERN_SIZE_LIMIT = 10 * 1024
PATTERN_DFA_SIZE_LIMIT = 64 * 1024
REQUEST_BODY_MAX_VALUES = 100_000
REQUEST_BODY_MAX_DEPTH = 128
REQUEST_BODY_MAX_TEXT_BYTES = 65_536
REQUEST_BODY_MAX_INTEGER = 2**256
MAX_SAFE_INTEGER = 2**53 - 1
DECIMAL_MIN_MAGNITUDE = 1e-05
DECIMAL_MAX_MAGNITUDE = 1e15
# The time validating one body may take, besides its pattern scans, and the worst cost of
# each part, measured on an Apple M2 with jsonschema-rs 0.58.
REQUEST_BODY_BUDGET_NS = 75_000_000
VALUE_COST_NS = 700  # walking one value, here and in jsonschema-rs
SUBSCHEMA_COST_NS = 600  # one subschema applied to a value, apart from the costs below
NUMBER_COST_NS = 7_500  # one exact comparison of a number
ENTRY_COST_NS = 20  # one enum, const or dependency entry scanned
UNIQUE_ITEMS_COST_NS = 21_000  # comparing a value with up to 14 others, for one uniqueItems

# A location in a schema: its JSON Pointer's reference tokens.
type Pointer = tuple[str, ...]
# A location in a body: its parent's location and its key or index, or None at the root.
# Linked rather than copied, so that walking a body costs the same at any depth.
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
_MISMATCH = "request body does not match the request schema"


class _Subschema(NamedTuple):
    """Where one subschema leads, and what it costs on the value it applies to.

    `same_value` holds, for each subschema it applies to the same value, that subschema,
    where it is applied from (the child itself for an applicator, the `$ref` or
    `$dynamicRef` keyword for a reference) and whether it is an applicator. The next
    fields lead to the value's members.
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
    unique_items: int
    compares_numbers: bool
    branches: bool  # anyOf or oneOf


@dataclass(frozen=True)
class _BodyBudget:
    """What validating a body against one schema costs, from the schema's analysis."""

    root_ns: int
    member_ns: int  # any value but the root, with its key
    unique_items_ns: int  # per value and level of nesting, for the uniqueItems above it
    compares_numbers: bool
    matches_patterns: bool
    locates_errors: bool

    @property
    def max_values(self) -> int:
        fitting = 1 + (REQUEST_BODY_BUDGET_NS - self.root_ns) // self.member_ns
        return min(REQUEST_BODY_MAX_VALUES, fitting)


class _Compiled(NamedTuple):
    validator: jsonschema_rs.Draft202012Validator
    budget: _BodyBudget


class _OverBudgetError(Exception):
    """More than REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE subschemas apply to one value."""


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
    """Accept `schema` only if the invoke path can validate request bodies with it cheaply.

    It must take at most REQUEST_SCHEMA_MAX_BYTES of compact JSON, hold only valid
    Unicode and bounded numbers, use draft 2020-12 throughout, refer only to its own
    subschemas, keep within the per-value limits of the module docstring, and be valid
    under the draft's meta-schema. Raises ValueError naming the first rule broken and
    where in the schema, or in a request body it would validate, it is broken.
    """
    canonical_schema = _canonical(schema)
    if len(canonical_schema.encode("utf-8", "surrogatepass")) > REQUEST_SCHEMA_MAX_BYTES:
        msg = f"request_schema must be at most {REQUEST_SCHEMA_MAX_BYTES} bytes of compact JSON"
        raise ValueError(msg)
    for value, _, where in _json_values(schema):
        problem = _value_problem(value, compared=True)
        if problem:
            msg = f"request_schema {problem} at {_where_location(where)}"
            raise ValueError(msg)
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
    return _compile(_canonical(schema)).validator


def validate_request_body(*, schema: JsonObject, body: JsonValue) -> None:
    """Validate a request body against its endpoint's accepted request schema.

    Checks the body against the schema's body budget, then validates it with the cached
    compiled validator. Raises InvalidInputError naming the bound broken, or the first
    error and where it is when that is cheap to find, or else that the body does not
    match.
    """
    compiled = _compile(_canonical(schema))
    _check_body(body, compiled.budget)
    if not compiled.budget.locates_errors:
        if not compiled.validator.is_valid(body):
            raise InvalidInputError(_MISMATCH)
        return
    try:
        compiled.validator.validate(body)
    except jsonschema_rs.ValidationError as exc:
        location = _location(tuple(str(part) for part in exc.instance_path))
        msg = f"{_MISMATCH}: {exc.message} at {location}"
        raise InvalidInputError(msg) from None


def _canonical(schema: JsonObject) -> str:
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@lru_cache(maxsize=1024)
def _compile(canonical_schema: str) -> _Compiled:
    schema = json.loads(canonical_schema)
    budget = _ValueCosts(_subschemas(schema)).budget()
    validator = jsonschema_rs.Draft202012Validator(
        schema,
        # Never fetch a remote $ref: the schema is the provider's input.
        offline=True,
        # Linear-time matching with bounded programs, so no pattern can stall the API.
        pattern_options=jsonschema_rs.RegexOptions(
            size_limit=PATTERN_SIZE_LIMIT,
            dfa_size_limit=PATTERN_DFA_SIZE_LIMIT,
        ),
    )
    return _Compiled(validator, budget)


def _schema_error_message(exc: jsonschema_rs.ValidationError) -> str:
    path = tuple(str(part) for part in exc.instance_path)
    if exc.kind.as_dict() == {"format": "regex"}:
        # A refused `pattern` is the value itself; a refused patternProperties key is the
        # subschema's key.
        pattern = exc.instance if isinstance(exc.instance, str) else path[-1]
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
            # analysis must follow the same one.
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
    # The empty fragment names the same meta-schema.
    if subschema.get("$schema", DRAFT_2020_12) not in (DRAFT_2020_12, f"{DRAFT_2020_12}#"):
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
        for value, _, _ in _json_values(item)
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
        # An integer bound compares cheaply; a decimal one, even a whole number, does not.
        numbers=compared_numbers
        + sum(isinstance(subschema.get(keyword), float) for keyword in _NUMBER_KEYWORDS),
        entries=len(scalars)
        - compared_numbers
        + sum(
            len(dependencies)
            for keyword in _DEPENDENCY_KEYWORDS
            if isinstance(dependencies := subschema.get(keyword), dict)
        ),
        unique_items=int(subschema.get("uniqueItems") is True),
        # uniqueItems compares the items with each other exactly, numbers included.
        compares_numbers=bool(compared_numbers)
        or subschema.get("uniqueItems") is True
        or any(keyword in subschema for keyword in _NUMBER_KEYWORDS),
        branches="anyOf" in subschema or "oneOf" in subschema,
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
    """Checks the per-value limits for every value a request body can hold, and derives the
    schema's body budget.

    A body value meets the subschemas its parent's subschemas apply to it by property,
    pattern or item, and, from each of those, the subschemas they apply to the same value
    through applicators and references. The analysis explores, breadth first down to
    REQUEST_BODY_MAX_DEPTH, every distinct set of subschemas a value can meet, so a
    recursive `$ref` passes only if those sets stop growing: one that fans out again at
    each level of a body soon exceeds the subschema limit.
    """

    def __init__(self, subschemas: dict[Pointer, _Subschema]) -> None:
        self._subschemas = subschemas
        # Per subschema: the subschemas it applies to its value, itself included, and how
        # deeply applicators nest among them.
        self._applied: dict[Pointer, tuple[Pointer, ...]] = {}
        self._nesting: dict[Pointer, int] = {}

    def budget(self) -> _BodyBudget:
        root = self._met([()], where=None)
        seen = {root}
        pending: deque[tuple[tuple[Pointer, ...], int, Where]] = deque([(root, 0, None)])
        met_anywhere = set(root)
        member_ns = unique_items = steps = 0
        while pending:
            met, depth, where = pending.popleft()
            subschemas = self._met_subschemas(met)
            _check_compared(subschemas, key_patterns=0, where=where, keys=False)
            keys_met = self._met(
                [subschema.property_names for subschema in subschemas], where=where, keys=True
            )
            keys = self._met_subschemas(keys_met)
            key_patterns = sum(len(subschema.pattern_properties) for subschema in subschemas)
            _check_compared(keys, key_patterns=key_patterns, where=where, keys=True)
            met_anywhere.update(keys_met)
            unique_items = max(
                unique_items, sum(subschema.unique_items for subschema in subschemas)
            )
            steps += len(met) + len(keys_met)
            # No body the budget accepts holds a member deeper than this.
            members = _members(subschemas) if depth < REQUEST_BODY_MAX_DEPTH else ()
            for member, pointers in members:
                member_where = (where, member)
                member_met = self._met(pointers, where=member_where)
                member_ns = max(member_ns, _cost_ns(self._met_subschemas(member_met) + keys))
                steps += len(member_met)
                if member_met and member_met not in seen:
                    seen.add(member_met)
                    met_anywhere.update(member_met)
                    pending.append((member_met, depth + 1, member_where))
            if steps > REQUEST_SCHEMA_MAX_ANALYSIS_STEPS:
                msg = (
                    "request_schema combines its subschemas in too many ways to bound the "
                    "cost of validating a request body"
                )
                raise ValueError(msg)
        reached = self._met_subschemas(tuple(met_anywhere))
        matches_patterns = any(
            subschema.patterns or subschema.pattern_properties for subschema in reached
        )
        return _BodyBudget(
            root_ns=VALUE_COST_NS + _cost_ns(self._met_subschemas(root)),
            member_ns=VALUE_COST_NS + member_ns,
            unique_items_ns=UNIQUE_ITEMS_COST_NS * unique_items,
            compares_numbers=any(subschema.compares_numbers for subschema in reached),
            matches_patterns=matches_patterns,
            locates_errors=not matches_patterns
            and not any(subschema.branches for subschema in reached),
        )

    def _met_subschemas(self, met: tuple[Pointer, ...]) -> list[_Subschema]:
        return [self._subschemas[pointer] for pointer in met]

    def _met(
        self,
        pointers: list[Pointer | None],
        *,
        where: Where,
        keys: bool = False,
    ) -> tuple[Pointer, ...]:
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
                f"request body (at {_where_location(where, keys=keys)})"
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
        # Bounds the recursion before any closure is complete: a longer chain applies more
        # subschemas to one value than the limit allows.
        if len(route) > REQUEST_SCHEMA_MAX_SUBSCHEMAS_PER_VALUE:
            raise _OverBudgetError
        applied = [pointer]
        nesting = 0
        for target, source, through_applicator in self._subschemas[pointer].same_value:
            if target in route:
                msg = (
                    "request_schema refers back to a subschema already applied to the same "
                    f"value at {_pointer(source)}"
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


def _check_compared(
    subschemas: list[_Subschema],
    *,
    key_patterns: int,
    where: Where,
    keys: bool,
) -> None:
    """Check what one value is compared with: patterns, numbers and other entries."""
    for count, limit, compared in (
        (
            key_patterns + sum(subschema.patterns for subschema in subschemas),
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
            msg = f"request_schema {compared} (at {_where_location(where, keys=keys)})"
            raise ValueError(msg)


def _cost_ns(subschemas: list[_Subschema]) -> int:
    """The worst cost of applying `subschemas` to one value, from the measured costs."""
    return sum(
        SUBSCHEMA_COST_NS + NUMBER_COST_NS * subschema.numbers + ENTRY_COST_NS * subschema.entries
        for subschema in subschemas
    )


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
    additional = [subschema.additional_properties for subschema in subschemas]
    yield "{any property}", [*additional, *patterned]
    contained = [subschema.contains for subschema in subschemas]
    for index in range(max((len(subschema.prefix_items) for subschema in subschemas), default=0)):
        indexed = [
            subschema.prefix_items[index]
            if index < len(subschema.prefix_items)
            else subschema.items
            for subschema in subschemas
        ]
        yield str(index), [*indexed, *contained]
    yield "{any item}", [*(subschema.items for subschema in subschemas), *contained]


def _check_body(body: JsonValue, budget: _BodyBudget) -> None:
    """Reject a request body beyond `budget` or the body bounds of the module docstring.

    Each container's members are counted before they are walked, so an oversized body is
    refused after at most `budget.max_values` values.
    """
    max_values = budget.max_values
    values = 1
    cost_ns = budget.root_ns
    text_bytes = 0
    for value, depth, where in _json_values(body):
        if depth:
            cost_ns += budget.member_ns + budget.unique_items_ns * depth
            if cost_ns > REQUEST_BODY_BUDGET_NS:
                msg = (
                    "request body is too large to validate against the request schema "
                    "within its budget"
                )
                raise InvalidInputError(msg)
        if isinstance(value, dict | list):
            if depth >= REQUEST_BODY_MAX_DEPTH:
                msg = (
                    f"request body nests more than {REQUEST_BODY_MAX_DEPTH} levels "
                    f"at {_where_location(where)}"
                )
                raise InvalidInputError(msg)
            values += len(value)
            if values > max_values:
                msg = f"request body holds more than {max_values} values"
                raise InvalidInputError(msg)
        problem = _value_problem(value, compared=budget.compares_numbers)
        if problem:
            msg = f"request body {problem} at {_where_location(where)}"
            raise InvalidInputError(msg)
        if budget.matches_patterns:
            if isinstance(value, str):
                text_bytes += len(value.encode())
            elif isinstance(value, dict):
                text_bytes += sum(len(key.encode()) for key in value)
            if text_bytes > REQUEST_BODY_MAX_TEXT_BYTES:
                msg = (
                    f"request body holds more than {REQUEST_BODY_MAX_TEXT_BYTES} bytes of "
                    "text in its strings and keys"
                )
                raise InvalidInputError(msg)


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


def _value_problem(value: JsonValue, *, compared: bool) -> str | None:
    """What unfits a value for validation: invalid Unicode, or a number out of range.

    Numbers the schema `compared` must fit the narrower range jsonschema-rs compares
    cheaply.
    """
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
    if not compared:
        if isinstance(value, int) and abs(value) > REQUEST_BODY_MAX_INTEGER:
            return f"number {value} is out of range (integers must be at most 2^256 in magnitude)"
        return None
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


def _where_location(where: Where, *, keys: bool = False) -> str:
    tokens: list[str] = []
    while where is not None:
        where, token = where
        tokens.append(str(token))
    location = _location(tuple(reversed(tokens)))
    return f"the keys of {location}" if keys else location


def _location(pointer: Pointer) -> str:
    return _pointer(pointer) if pointer else "the root"


def _pointer(pointer: Pointer) -> str:
    """The JSON Pointer (RFC 6901) of a location."""
    return "".join("/" + token.replace("~", "~0").replace("/", "~1") for token in pointer)
