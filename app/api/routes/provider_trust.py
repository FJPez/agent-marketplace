from fastapi import APIRouter, Response, status

from app.api.deps.auth import CurrentJwtActor
from app.api.deps.database import SessionDep
from app.api.deps.settings import SettingsDep
from app.schemas.provider_trust import IssuedSigningSecretResponse, SigningSecretResponse
from app.services import provider_signing_secrets

router = APIRouter(prefix="/provider", tags=["provider-trust"])

# The signing secret routes take a JWT only, like the API key routes: an API key can
# neither mint signing material nor manage it.
_SIGNING_SECRET_ABOUT = (
    "The marketplace signs every request it sends to the provider's upstreams with the "
    "provider's signing secret, so the provider can verify it came from the marketplace. "
    "The secret is shown once, in this response; store it at once."
)


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
    response.headers["Cache-Control"] = "no-store"
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
        f"{_SIGNING_SECRET_ABOUT} The replaced secret keeps signing beside the new one "
        "until `previous_expires_at`, so deploy the new secret before then; rotating "
        "again ends that grace period at once."
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
) -> IssuedSigningSecretResponse:
    stored, secret = await provider_signing_secrets.rotate_signing_secret(
        session=session,
        settings=settings,
        account_id=actor.account_id,
    )
    response.headers["Cache-Control"] = "no-store"
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
