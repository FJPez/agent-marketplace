from __future__ import annotations

type JsonPrimitive = str | int | float | bool | None
type JsonValue = dict[str, JsonValue] | list[JsonValue] | JsonPrimitive
type JsonObject = dict[str, JsonValue]


def to_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [to_json_value(item) for item in value]
    if isinstance(value, dict):
        result: JsonObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = "JSON object keys must be strings"
                raise TypeError(msg)
            result[key] = to_json_value(item)
        return result
    msg = "value is not JSON-serializable"
    raise TypeError(msg)


def to_json_object(value: JsonObject | dict[str, object]) -> JsonObject:
    return {key: to_json_value(item) for key, item in value.items()}
