from typing import Annotated

from fastapi import Depends, Header

from app.api.deps.database import SessionDep
from app.api.deps.settings import SettingsDep
from app.core.actor import ActorContext
from app.core.errors import PermissionDeniedError, UnauthenticatedError
from app.services.auth import resolve_actor, resolve_jwt_actor

AUTHORIZATION_HEADER = "Authorization"

AuthorizationHeader = Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)]


async def get_optional_current_actor(
    session: SessionDep,
    settings: SettingsDep,
    authorization: AuthorizationHeader = None,
) -> ActorContext | None:
    if authorization is None:
        return None

    return await resolve_actor(session=session, settings=settings, authorization=authorization)


async def get_current_actor(
    session: SessionDep,
    settings: SettingsDep,
    authorization: AuthorizationHeader = None,
) -> ActorContext:
    if authorization is None:
        raise UnauthenticatedError(f"{AUTHORIZATION_HEADER} header is required")

    return await resolve_actor(session=session, settings=settings, authorization=authorization)


async def get_current_jwt_actor(
    session: SessionDep,
    settings: SettingsDep,
    authorization: AuthorizationHeader = None,
) -> ActorContext:
    if authorization is None:
        raise UnauthenticatedError(f"{AUTHORIZATION_HEADER} header is required")

    return await resolve_jwt_actor(
        session=session,
        settings=settings,
        authorization=authorization,
    )


async def get_admin_actor(
    actor: Annotated[ActorContext, Depends(get_current_actor)],
) -> ActorContext:
    if not actor.is_admin:
        raise PermissionDeniedError("admin privileges required")
    return actor


CurrentActor = Annotated[ActorContext, Depends(get_current_actor)]
CurrentJwtActor = Annotated[ActorContext, Depends(get_current_jwt_actor)]
OptionalCurrentActor = Annotated[ActorContext | None, Depends(get_optional_current_actor)]
AdminActor = Annotated[ActorContext, Depends(get_admin_actor)]
