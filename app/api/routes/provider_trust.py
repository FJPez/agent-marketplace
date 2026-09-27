from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from app.api.deps.auth import CurrentActor, CurrentJwtActor
from app.api.deps.database import SessionDep
from app.api.deps.settings import SettingsDep
from app.db.models import ProviderSigningSecret
from app.schemas.provider_trust import (
    DomainVerificationResponse,
    IssuedSigningSecretResponse,
    SigningSecretResponse,
)
from app.services import domain_control, provider_signing_secrets

router = APIRouter(prefix="/provider", tags=["provider-trust"])

_SIGNING_SECRET_ABOUT = (
    "The marketplace signs every request it sends to the provider's upstreams with the "
    "provider's signing secret, so the provider can verify it came from the marketplace. "
    "The secret is shown once, in this response; store it at once."
)

_RotationIdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        description=(
            "A fresh random value, such as a UUID, for each rotation. Retrying with the "
            "same key returns the secret that rotation issued instead of rotating again."
        ),
    ),
]


def _issued(
    response: Response,
    stored: ProviderSigningSecret,
    secret: str,
) -> IssuedSigningSecretResponse:
    """The response of a route that issues a secret, the only kind that carries one."""
    # No cache along the way may keep the secret.
    response.headers["Cache-Control"] = "no-store"
    return IssuedSigningSecretResponse(
        issued_at=stored.issued_at,
        previous_expires_at=stored.previous_expires_at,
        secret=secret,
    )


# The signing secret routes take a JWT only, like the API key routes: an API key can
# neither mint signing material nor manage it.
@router.post(
    "/signing-secret",
    response_model=IssuedSigningSecretResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create the provider's signing secret",
    description=_SIGNING_SECRET_ABOUT,
    responses={
        201: {"description": "Signing secret created; the response carries it once."},
        403: {"description": "A non-JWT bearer token was supplied."},
        409: {"description": "The account already has a signing secret, or none can be issued."},
    },
)
async def create_signing_secret(
    actor: CurrentJwtActor,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
) -> IssuedSigningSecretResponse:
    stored, secret = await provider_signing_secrets.create_signing_secret(
        session=session,
        settings=settings,
        account_id=actor.account_id,
    )
    return _issued(response, stored, secret)


@router.post(
    "/signing-secret/rotate",
    response_model=IssuedSigningSecretResponse,
    summary="Rotate the provider's signing secret",
    description=(
        f"{_SIGNING_SECRET_ABOUT} The replaced secret keeps signing beside the new one "
        "until `previous_expires_at`, so deploy the new secret before then; rotating "
        "again ends that grace period at once. If a rotate response is lost, retry with "
        "the same `Idempotency-Key`: while the secret that rotation issued is still "
        "current, the retry returns it instead of rotating again."
    ),
    responses={
        200: {"description": "Signing secret rotated; the response carries the new one once."},
        403: {"description": "A non-JWT bearer token was supplied."},
        404: {"description": "The account has no signing secret yet."},
        409: {"description": "No signing secret can be issued."},
    },
)
async def rotate_signing_secret(
    actor: CurrentJwtActor,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
    idempotency_key: _RotationIdempotencyKey = None,
) -> IssuedSigningSecretResponse:
    stored, secret = await provider_signing_secrets.rotate_signing_secret(
        session=session,
        settings=settings,
        account_id=actor.account_id,
        idempotency_key=idempotency_key,
    )
    return _issued(response, stored, secret)


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


# Unlike the signing secret routes, this one accepts an API key too: the record it
# returns is published in DNS, so it is no secret.
@router.post(
    "/domain-verification",
    response_model=DomainVerificationResponse,
    summary="Get or create the provider's domain verification record",
    description=(
        "Returns the TXT record that proves the provider controls its upstream hosts, "
        "creating the provider's token on the first call. Publish it at "
        "`<record_label>.<host>` for every upstream host; publishing a service checks "
        "each of its hosts. A new or changed record can take minutes to be visible, "
        "longer after a failed check because resolvers cache the miss (negative "
        "caching). Accepts a JWT or an API key."
    ),
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
