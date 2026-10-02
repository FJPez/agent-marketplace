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
# The provider proxy's per-listing timeout cap (spec section 12).
ENDPOINT_TIMEOUT_MAX_SECONDS = 30
DEFAULT_RESPONSE_CONTENT_TYPE = "application/json"
UPSTREAM_PATH_MAX_LENGTH = 2000
HTTP_METHOD_MAX_LENGTH = 16
# An RFC 6838 type/subtype (each at most 127 characters) without parameters; a
# wildcard such as text/* is not a concrete response type.
MEDIA_TYPE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$",
)


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


def normalize_media_type(value: str) -> str:
    normalized_value = value.strip().lower()
    if MEDIA_TYPE_PATTERN.fullmatch(normalized_value) is None:
        msg = "response_content_type must be a media type such as text/plain, without parameters"
        raise ValueError(msg)
    return normalized_value
