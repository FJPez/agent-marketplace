from __future__ import annotations

import hashlib
import json


def hash_request_body(body: object) -> str:
    """Hash a parsed JSON-compatible request body using canonical JSON encoding."""
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
