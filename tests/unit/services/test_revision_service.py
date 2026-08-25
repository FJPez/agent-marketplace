from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.db.models.pricing_model import PricingModel
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.services.revision_service import (
    RevisionService,
    UpdateImpact,
    build_contract_snapshot,
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
        timeout_seconds=30,
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
        timeout_seconds=15,
        is_enabled=False,
    )
    second_endpoint.pricing = PricingModel(
        endpoint_id=second_endpoint.id,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=2500,
        currency="USD",
    )
    service.endpoints = [second_endpoint, first_endpoint]
    return service


def test_classify_endpoint_update_marks_contract_fields_as_material() -> None:
    impact = RevisionService.classify_endpoint_update(
        {"request_schema": {"type": "object"}},
    )

    assert impact is UpdateImpact.MATERIAL


def test_classify_endpoint_update_marks_pricing_as_material() -> None:
    impact = RevisionService.classify_endpoint_update(
        {"pricing": {"amount_minor": 100, "currency": "USD"}},
    )

    assert impact is UpdateImpact.MATERIAL


def test_classify_endpoint_update_marks_descriptive_fields_as_non_material() -> None:
    impact = RevisionService.classify_endpoint_update(
        {"summary": "Updated summary", "description": "Updated description"},
    )

    assert impact is UpdateImpact.NON_MATERIAL


def test_classify_service_update_treats_current_patchable_fields_as_non_material() -> None:
    impact = RevisionService.classify_service_update(
        {"name": "Updated name", "summary": "Updated summary"},
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
                "pricing": {
                    "pricing_type": "fixed_per_call",
                    "amount_minor": 2500,
                    "currency": "USD",
                },
                "timeout_seconds": 15,
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
                "pricing": {
                    "pricing_type": "free",
                    "amount_minor": None,
                    "currency": None,
                },
                "timeout_seconds": 30,
                "is_enabled": True,
            },
        ],
    }
