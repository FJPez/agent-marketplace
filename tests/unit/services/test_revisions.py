import pytest
from tests.fixtures.settings import TEST_PRICE_TERMS

from app.core.enums import AccessMode, ServiceLifecycle
from app.db.models.listing_price import ListingPrice
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.services.revisions import (
    UpdateImpact,
    build_contract_snapshot,
    classify_endpoint_update,
)


def _service() -> Service:
    service = Service(
        id=101,
        provider_account_id=42,
        slug="translation-service",
        name="Translation Service",
        summary="Translate short text",
        description="Human-readable description",
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    first_endpoint = ServiceEndpoint(
        id=201,
        service_id=service.id,
        key="translate",
        name="Translate",
        summary="Translate text",
        description="Translate one payload",
        access_mode=AccessMode.FREE,
        request_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        response_schema={"type": "object", "properties": {"translated": {"type": "string"}}},
        response_content_type="application/json",
        timeout_seconds=30,
        supports_idempotency=False,
        is_enabled=True,
    )
    second_endpoint = ServiceEndpoint(
        id=202,
        service_id=service.id,
        key="detect-language",
        name="Detect Language",
        summary="Detect language",
        description="Detect source language",
        access_mode=AccessMode.PAID,
        request_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        response_schema={"type": "object", "properties": {"language": {"type": "string"}}},
        response_content_type="text/plain",
        timeout_seconds=15,
        supports_idempotency=True,
        is_enabled=False,
    )
    second_endpoint.current_price = ListingPrice(
        id=301,
        endpoint_id=second_endpoint.id,
        version=2,
        amount=25_000,
        **TEST_PRICE_TERMS,
    )
    service.endpoints = [second_endpoint, first_endpoint]
    return service


def test_classify_endpoint_update_marks_contract_fields_as_material() -> None:
    impact = classify_endpoint_update(
        {"request_schema": {"type": "object"}},
    )

    assert impact is UpdateImpact.MATERIAL


def test_classify_endpoint_update_marks_price_as_material() -> None:
    impact = classify_endpoint_update({"price": 25_000})

    assert impact is UpdateImpact.MATERIAL


@pytest.mark.parametrize("field", ["response_content_type", "supports_idempotency"])
def test_classify_endpoint_update_marks_invocation_fields_as_material(field: str) -> None:
    assert classify_endpoint_update({field}) is UpdateImpact.MATERIAL


def test_classify_endpoint_update_marks_descriptive_fields_as_non_material() -> None:
    impact = classify_endpoint_update(
        {"summary": "Updated summary", "description": "Updated description"},
    )

    assert impact is UpdateImpact.NON_MATERIAL


def test_build_contract_snapshot_keeps_only_contract_affecting_fields() -> None:
    snapshot = build_contract_snapshot(_service())

    assert snapshot == {
        "service": {
            "id": 101,
            "slug": "translation-service",
        },
        "endpoints": [
            {
                "id": 202,
                "key": "detect-language",
                "access_mode": "paid",
                "request_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
                "response_schema": {
                    "type": "object",
                    "properties": {"language": {"type": "string"}},
                },
                "response_content_type": "text/plain",
                "price": {"id": 301, "version": 2},
                "timeout_seconds": 15,
                "supports_idempotency": True,
                "is_enabled": False,
            },
            {
                "id": 201,
                "key": "translate",
                "access_mode": "free",
                "request_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
                "response_schema": {
                    "type": "object",
                    "properties": {"translated": {"type": "string"}},
                },
                "response_content_type": "application/json",
                "price": None,
                "timeout_seconds": 30,
                "supports_idempotency": False,
                "is_enabled": True,
            },
        ],
    }
