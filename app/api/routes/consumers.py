from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor
from app.db.session import get_db_session
from app.schemas.consumer import ConsumerProfileCreateRequest, ConsumerProfileResponse
from app.services.consumer_identity_service import ConsumerIdentityService
from app.services.identity_errors import IdentityConflictError

router = APIRouter(prefix="/consumers", tags=["consumers"])


def _raise_conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.post(
    "",
    response_model=ConsumerProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_consumer_profile(
    request: ConsumerProfileCreateRequest,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConsumerProfileResponse:
    service = ConsumerIdentityService(session)

    try:
        profile = await service.create_profile(actor, request)
    except IdentityConflictError as exc:
        raise _raise_conflict(str(exc)) from exc

    return ConsumerProfileResponse.model_validate(profile)
