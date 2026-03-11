from __future__ import annotations

import hashlib
import json

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


def hash_request_body(body: JsonValue) -> str:
    try:
        payload = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        msg = "request body must be JSON-compatible"
        raise ValueError(msg) from exc

    return hashlib.sha256(payload).hexdigest()
