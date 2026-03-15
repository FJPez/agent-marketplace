from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import AdminActor
from app.db.session import get_db_session
from app.schemas.admin import ModerationActionRequest, ModerationActionResponse
from app.services.moderation_service import (
    InvalidModerationTransitionError,
    ModeratedServiceNotFoundError,
    ModerationService,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ModeratedServiceNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, InvalidModerationTransitionError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/services/{service_id}/suspend",
    response_model=ModerationActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def suspend_service(
    service_id: int,
    request: ModerationActionRequest,
    actor: AdminActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ModerationActionResponse:
    service = ModerationService(session)
    try:
        action = await service.suspend_service(
            service_id=service_id,
            reason=request.reason,
            actor=actor,
        )
    except (InvalidModerationTransitionError, ModeratedServiceNotFoundError) as exc:
        raise _to_http_exception(exc) from exc
    return ModerationActionResponse.from_model(action)


@router.post(
    "/services/{service_id}/restore",
    response_model=ModerationActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def restore_service(
    service_id: int,
    request: ModerationActionRequest,
    actor: AdminActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ModerationActionResponse:
    service = ModerationService(session)
    try:
        action = await service.restore_service(
            service_id=service_id,
            reason=request.reason,
            actor=actor,
        )
    except (InvalidModerationTransitionError, ModeratedServiceNotFoundError) as exc:
        raise _to_http_exception(exc) from exc
    return ModerationActionResponse.from_model(action)


@router.post(
    "/services/{service_id}/delist",
    response_model=ModerationActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def delist_service(
    service_id: int,
    request: ModerationActionRequest,
    actor: AdminActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ModerationActionResponse:
    service = ModerationService(session)
    try:
        action = await service.delist_service(
            service_id=service_id,
            reason=request.reason,
            actor=actor,
        )
    except (InvalidModerationTransitionError, ModeratedServiceNotFoundError) as exc:
        raise _to_http_exception(exc) from exc
    return ModerationActionResponse.from_model(action)


@router.get("/moderation/actions", response_model=list[ModerationActionResponse])
async def list_moderation_actions(
    actor: AdminActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service_id: Annotated[int, Query(gt=0)],
) -> list[ModerationActionResponse]:
    service = ModerationService(session)
    actions = await service.list_actions(service_id=service_id)
    return [ModerationActionResponse.from_model(action) for action in actions]
