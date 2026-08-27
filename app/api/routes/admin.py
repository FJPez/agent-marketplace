from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps.auth import AdminActor
from app.api.deps.database import SessionDep
from app.schemas.admin import ModerationActionRequest, ModerationActionResponse
from app.services import moderation

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/services/{service_id}/suspend",
    response_model=ModerationActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Suspend a service",
    description="Creates a moderation action that suspends a service from marketplace use.",
    responses={
        201: {"description": "Service suspended successfully."},
        404: {"description": "The requested service does not exist."},
        409: {"description": "The requested service cannot transition to suspended."},
    },
)
async def suspend_service(
    service_id: int,
    request: ModerationActionRequest,
    admin: AdminActor,
    session: SessionDep,
) -> ModerationActionResponse:
    action = await moderation.suspend_service(
        session=session,
        service_id=service_id,
        actor_account_id=admin.account_id,
        reason=request.reason,
    )
    return ModerationActionResponse.from_model(action)


@router.post(
    "/services/{service_id}/restore",
    response_model=ModerationActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Restore a service",
    description="Creates a moderation action that restores a previously suspended service.",
    responses={
        201: {"description": "Service restored successfully."},
        404: {"description": "The requested service does not exist."},
        409: {"description": "The requested service cannot transition to restored."},
    },
)
async def restore_service(
    service_id: int,
    request: ModerationActionRequest,
    admin: AdminActor,
    session: SessionDep,
) -> ModerationActionResponse:
    action = await moderation.restore_service(
        session=session,
        service_id=service_id,
        actor_account_id=admin.account_id,
        reason=request.reason,
    )
    return ModerationActionResponse.from_model(action)


@router.post(
    "/services/{service_id}/delist",
    response_model=ModerationActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Delist a service",
    description="Creates a moderation action that delists a service from public discovery.",
    responses={
        201: {"description": "Service delisted successfully."},
        404: {"description": "The requested service does not exist."},
        409: {"description": "The requested service cannot transition to delisted."},
    },
)
async def delist_service(
    service_id: int,
    request: ModerationActionRequest,
    admin: AdminActor,
    session: SessionDep,
) -> ModerationActionResponse:
    action = await moderation.delist_service(
        session=session,
        service_id=service_id,
        actor_account_id=admin.account_id,
        reason=request.reason,
    )
    return ModerationActionResponse.from_model(action)


@router.get(
    "/moderation/actions",
    response_model=list[ModerationActionResponse],
    summary="List moderation actions",
    description="Lists moderation actions recorded for a given service identifier.",
    responses={200: {"description": "Moderation actions returned successfully."}},
)
async def list_moderation_actions(
    admin: AdminActor,
    session: SessionDep,
    service_id: Annotated[int, Query(gt=0)],
) -> list[ModerationActionResponse]:
    actions = await moderation.list_actions(session=session, service_id=service_id)
    return [ModerationActionResponse.from_model(action) for action in actions]
