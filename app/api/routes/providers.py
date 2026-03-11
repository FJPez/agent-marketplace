from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor, OptionalCurrentActor
from app.db.session import get_db_session
from app.schemas.provider import (
    ProviderProfileCreateRequest,
    ProviderProfileResponse,
    ProviderProfileUpdateRequest,
)
from app.services.identity_errors import (
    IdentityConflictError,
    IdentityNotFoundError,
    IdentityValidationError,
)
from app.services.provider_identity_service import ProviderIdentityService

router = APIRouter(prefix="/providers", tags=["providers"])


def _raise_conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _raise_not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _raise_validation_error(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=detail,
    )


@router.post(
    "",
    response_model=ProviderProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider_profile(
    request: ProviderProfileCreateRequest,
    actor: OptionalCurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProviderProfileResponse:
    service = ProviderIdentityService(session)

    try:
        profile = await service.create_profile(actor, request)
    except IdentityConflictError as exc:
        raise _raise_conflict(str(exc)) from exc

    return ProviderProfileResponse.model_validate(profile)


@router.get("/me", response_model=ProviderProfileResponse)
async def get_provider_profile(
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProviderProfileResponse:
    service = ProviderIdentityService(session)

    try:
        profile = await service.get_profile(actor)
    except IdentityNotFoundError as exc:
        raise _raise_not_found("provider profile not found") from exc

    return ProviderProfileResponse.model_validate(profile)


@router.patch("/me", response_model=ProviderProfileResponse)
async def update_provider_profile(
    request: ProviderProfileUpdateRequest,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProviderProfileResponse:
    service = ProviderIdentityService(session)

    try:
        profile = await service.update_profile(actor, request)
    except IdentityNotFoundError as exc:
        raise _raise_not_found("provider profile not found") from exc
    except IdentityValidationError as exc:
        raise _raise_validation_error(str(exc)) from exc

    return ProviderProfileResponse.model_validate(profile)
