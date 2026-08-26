import pytest
from pydantic import ValidationError

from app.core.service_fields import SLUG_MAX_LENGTH
from app.schemas.service_ref import SERVICE_ID_MAX, parse_public_service_ref


def test_parse_public_service_ref_reads_numeric_identifiers_as_ids() -> None:
    ref = parse_public_service_ref("42")

    assert ref.id == 42
    assert ref.slug is None


def test_parse_public_service_ref_reads_non_numeric_identifiers_as_slugs() -> None:
    ref = parse_public_service_ref("translation-service")

    assert ref.id is None
    assert ref.slug == "translation-service"


def test_parse_public_service_ref_accepts_maximum_length_slug() -> None:
    longest_slug = "a" * SLUG_MAX_LENGTH

    ref = parse_public_service_ref(longest_slug)

    assert ref.slug == longest_slug


def test_parse_public_service_ref_accepts_maximum_service_id() -> None:
    ref = parse_public_service_ref(str(SERVICE_ID_MAX))

    assert ref.id == SERVICE_ID_MAX


@pytest.mark.parametrize(
    "identifier",
    [
        "Translation-Service",
        "translation service",
        "translation_service",
        "-leading-dash",
        "a" * (SLUG_MAX_LENGTH + 1),
        str(SERVICE_ID_MAX + 1),
        "0",
        "",
    ],
)
def test_parse_public_service_ref_rejects_malformed_identifiers(identifier: str) -> None:
    with pytest.raises(ValidationError):
        parse_public_service_ref(identifier)
