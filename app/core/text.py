"""Shared text normalization rules enforced by both request schemas and services.

Each function raises ``ValueError`` so Pydantic can use it directly as an
``AfterValidator`` while services catch and re-raise ``InvalidInputError``.
"""

import re

TAG_TOKEN_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

SLUG_MAX_LENGTH = 255
TAG_MAX_LENGTH = 64
SERVICE_TAGS_MAX_COUNT = 20
SERVICE_NAME_MAX_LENGTH = 255
SERVICE_SUMMARY_MAX_LENGTH = 500
SERVICE_DESCRIPTION_MAX_LENGTH = 5000


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


def normalize_required_text(value: str, *, field_name: str, max_length: int) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        msg = f"{field_name} must not be blank"
        raise ValueError(msg)
    if len(normalized_value) > max_length:
        msg = f"{field_name} must be at most {max_length} characters"
        raise ValueError(msg)
    return normalized_value
