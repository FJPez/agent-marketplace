import pytest
from pydantic import TypeAdapter, ValidationError

from app.core.service_fields import SLUG_MAX_LENGTH
from app.schemas.service_ref import SERVICE_ID_MAX, PublicServiceRef

service_ref_adapter = TypeAdapter(PublicServiceRef)


def test_public_service_ref_reads_numeric_identifiers_as_ids() -> None:
    assert service_ref_adapter.validate_python("42") == 42


def test_public_service_ref_reads_non_numeric_identifiers_as_slugs() -> None:
    assert service_ref_adapter.validate_python("translation-service") == "translation-service"


def test_public_service_ref_accepts_maximum_length_slug() -> None:
    longest_slug = "a" * SLUG_MAX_LENGTH

    assert service_ref_adapter.validate_python(longest_slug) == longest_slug


def test_public_service_ref_accepts_maximum_service_id() -> None:
    assert service_ref_adapter.validate_python(str(SERVICE_ID_MAX)) == SERVICE_ID_MAX


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
def test_public_service_ref_rejects_malformed_identifiers(identifier: str) -> None:
    with pytest.raises(ValidationError):
        service_ref_adapter.validate_python(identifier)
