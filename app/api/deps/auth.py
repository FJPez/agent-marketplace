from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.config import get_settings
from app.db.session import get_db_session
from app.services.auth_resolution_service import (
    AuthResolutionError,
    AuthResolutionService,
    JwtAuthRequiredError,
)

AUTHORIZATION_HEADER = "Authorization"


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def get_optional_current_actor(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
) -> ActorContext | None:
    if authorization is None:
        return None

    service = AuthResolutionService(session, settings=get_settings())
    try:
        return await service.resolve_actor(authorization=authorization)
    except AuthResolutionError as exc:
        raise _unauthorized(str(exc)) from exc


async def get_current_actor(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
) -> ActorContext:
    if authorization is None:
        detail = f"{AUTHORIZATION_HEADER} header is required"
        raise _unauthorized(detail)

    service = AuthResolutionService(session, settings=get_settings())
    try:
        return await service.resolve_actor(authorization=authorization)
    except AuthResolutionError as exc:
        raise _unauthorized(str(exc)) from exc


async def get_current_jwt_actor(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
) -> ActorContext:
    if authorization is None:
        detail = f"{AUTHORIZATION_HEADER} header is required"
        raise _unauthorized(detail)

    service = AuthResolutionService(session, settings=get_settings())
    try:
        return await service.resolve_jwt_actor(authorization=authorization)
    except AuthResolutionError as exc:
        raise _unauthorized(str(exc)) from exc
    except JwtAuthRequiredError as exc:
        raise _forbidden(str(exc)) from exc


async def get_admin_actor(
    actor: Annotated[ActorContext, Depends(get_current_actor)],
) -> ActorContext:
    if not actor.is_admin:
        raise _forbidden("admin privileges required")
    return actor


CurrentActor = Annotated[ActorContext, Depends(get_current_actor)]
CurrentJwtActor = Annotated[ActorContext, Depends(get_current_jwt_actor)]
OptionalCurrentActor = Annotated[ActorContext | None, Depends(get_optional_current_actor)]
AdminActor = Annotated[ActorContext, Depends(get_admin_actor)]
