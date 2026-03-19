import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HmacAuthConfig:
    key_id: str
    secret: str


def get_hmac_auth_config(config: Mapping[str, object]) -> HmacAuthConfig | None:
    auth = config.get("auth")
    if not isinstance(auth, dict):
        return None
    auth_map = {str(key): value for key, value in auth.items()}

    auth_type = auth_map.get("type")
    key_id = auth_map.get("key_id")
    secret = auth_map.get("secret")
    if auth_type != "hmac_sha256" or not isinstance(key_id, str) or not isinstance(secret, str):
        return None

    return HmacAuthConfig(key_id=key_id, secret=secret)


def build_signed_headers(
    *,
    key_id: str,
    secret: str,
    http_method: str,
    path: str,
    request_hash: str,
    invocation_id: int,
    timestamp: str,
) -> dict[str, str]:
    canonical = "\n".join(
        [
            http_method,
            path,
            timestamp,
            request_hash,
            str(invocation_id),
        ],
    )
    signature = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Agent-Marketplace-Key-Id": key_id,
        "X-Agent-Marketplace-Timestamp": timestamp,
        "X-Agent-Marketplace-Request-Hash": request_hash,
        "X-Agent-Marketplace-Invocation-Id": str(invocation_id),
        "X-Agent-Marketplace-Signature": signature,
    }
