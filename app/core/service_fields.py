"""Field rules and constants for the service catalog.

Normalizers raise ``ValueError`` and are applied by Pydantic ``AfterValidator``s
on the request schemas; constants are shared with the ORM column definitions.
"""

import re
from urllib.parse import urlsplit

TAG_TOKEN_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

SLUG_MAX_LENGTH = 255
TAG_MAX_LENGTH = 64
SERVICE_TAGS_MAX_COUNT = 20
SERVICE_NAME_MAX_LENGTH = 255
SERVICE_SUMMARY_MAX_LENGTH = 500
SERVICE_DESCRIPTION_MAX_LENGTH = 5000
ENDPOINT_TIMEOUT_MAX_SECONDS = 3600
UPSTREAM_PATH_MAX_LENGTH = 2000
HTTP_METHOD_MAX_LENGTH = 16
CURRENCY_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")


def normalize_slug(value: str) -> str:
    normalized_value = value.strip()
    if len(normalized_value) > SLUG_MAX_LENGTH:
        msg = f"slug must be at most {SLUG_MAX_LENGTH} characters"
        raise ValueError(msg)
    if TAG_TOKEN_PATTERN.fullmatch(normalized_value) is None:
        msg = "slug must be a lowercase slug token"
        raise ValueError(msg)
    if normalized_value.isdigit():
        msg = "slug must include at least one lowercase letter"
        raise ValueError(msg)
    return normalized_value


def normalize_tag(value: str) -> str:
    normalized_value = value.strip().lower()
    if len(normalized_value) > TAG_MAX_LENGTH:
        msg = f"tags must be at most {TAG_MAX_LENGTH} characters"
        raise ValueError(msg)
    if TAG_TOKEN_PATTERN.fullmatch(normalized_value) is None:
        msg = "tags must be lowercase slug tokens"
        raise ValueError(msg)
    return normalized_value


def normalize_upstream_path(value: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        msg = "path must not be blank"
        raise ValueError(msg)
    if len(normalized_value) > UPSTREAM_PATH_MAX_LENGTH:
        msg = f"path must be at most {UPSTREAM_PATH_MAX_LENGTH} characters"
        raise ValueError(msg)
    if not normalized_value.startswith("/"):
        msg = "path must start with /"
        raise ValueError(msg)
    parsed = urlsplit(normalized_value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        msg = "path must be path-only and must not include scheme, host, query, or fragment"
        raise ValueError(msg)
    return normalized_value


def normalize_currency_code(value: str) -> str:
    normalized_value = value.strip()
    if CURRENCY_CODE_PATTERN.fullmatch(normalized_value) is None:
        msg = "currency must be a 3-letter uppercase currency code"
        raise ValueError(msg)
    return normalized_value
