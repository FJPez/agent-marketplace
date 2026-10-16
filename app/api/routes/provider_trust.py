from fastapi import APIRouter, status

from app.api.deps.auth import CurrentActor, CurrentJwtActor
from app.api.deps.database import SessionDep
from app.api.deps.settings import SettingsDep
from app.schemas.provider_trust import (
    DomainVerificationResponse,
    IssuedSigningSecretResponse,
    SigningSecretResponse,
)
from app.services import domain_control, provider_signing_secrets

router = APIRouter(prefix="/provider", tags=["provider-trust"])


# JWT only, like the API key routes: an API key cannot mint or manage signing material.
@router.post(
    "/signing-secret",
    response_model=IssuedSigningSecretResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create the provider's signing secret",
    description="Creates the provider's signing secret, returned only in this response.",
    responses={
        201: {"description": "Signing secret created."},
        403: {"description": "A non-JWT bearer token was supplied."},
        409: {"description": "The account already has a signing secret, or none can be issued."},
    },
)
async def create_signing_secret(
    actor: CurrentJwtActor,
    session: SessionDep,
    settings: SettingsDep,
) -> IssuedSigningSecretResponse:
    stored, secret = await provider_signing_secrets.create_signing_secret(
        session=session,
        settings=settings,
        account_id=actor.account_id,
    )
    return IssuedSigningSecretResponse(
        issued_at=stored.issued_at,
        previous_expires_at=stored.previous_expires_at,
        secret=secret,
    )


@router.post(
    "/signing-secret/rotate",
    response_model=IssuedSigningSecretResponse,
    summary="Rotate the provider's signing secret",
    description=(
        "Replaces the signing secret; the old one keeps signing until `previous_expires_at`."
    ),
    responses={
        200: {"description": "Signing secret rotated."},
        403: {"description": "A non-JWT bearer token was supplied."},
        404: {"description": "The account has no signing secret yet."},
        409: {"description": "No signing secret can be issued."},
    },
)
async def rotate_signing_secret(
    actor: CurrentJwtActor,
    session: SessionDep,
    settings: SettingsDep,
) -> IssuedSigningSecretResponse:
    stored, secret = await provider_signing_secrets.rotate_signing_secret(
        session=session,
        settings=settings,
        account_id=actor.account_id,
    )
    return IssuedSigningSecretResponse(
        issued_at=stored.issued_at,
        previous_expires_at=stored.previous_expires_at,
        secret=secret,
    )


@router.get(
    "/signing-secret",
    response_model=SigningSecretResponse,
    summary="Get the provider's signing secret status",
    description="Returns when the signing secret was issued; never the secret itself.",
    responses={
        200: {"description": "Signing secret status returned."},
        403: {"description": "A non-JWT bearer token was supplied."},
        404: {"description": "The account has no signing secret yet."},
    },
)
async def get_signing_secret(actor: CurrentJwtActor, session: SessionDep) -> SigningSecretResponse:
    stored = await provider_signing_secrets.get_signing_secret(
        session=session,
        account_id=actor.account_id,
    )
    return SigningSecretResponse.model_validate(stored)


# Also accepts an API key: the record is published in DNS, so it is no secret.
@router.post(
    "/domain-verification",
    response_model=DomainVerificationResponse,
    summary="Get or create the provider's domain verification record",
    description="Returns the TXT record that proves the provider controls its upstream hosts.",
    responses={200: {"description": "Domain verification record returned."}},
)
async def ensure_domain_verification_record(
    actor: CurrentActor,
    session: SessionDep,
) -> DomainVerificationResponse:
    token = await domain_control.ensure_domain_token(session=session, account_id=actor.account_id)
    return DomainVerificationResponse(
        record_label=domain_control.RECORD_LABEL,
        record_value=domain_control.record_value(token),
    )
