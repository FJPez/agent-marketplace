from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.api.deps.database import SessionDep
from app.core.actor import ActorContext
from app.core.config import get_settings
from app.services.auth import resolve_actor, resolve_jwt_actor

AUTHORIZATION_HEADER = "Authorization"


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def get_optional_current_actor(
    session: SessionDep,
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
) -> ActorContext | None:
    if authorization is None:
        return None

    return await resolve_actor(
        session=session, settings=get_settings(), authorization=authorization
    )


async def get_current_actor(
    session: SessionDep,
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
) -> ActorContext:
    if authorization is None:
        detail = f"{AUTHORIZATION_HEADER} header is required"
        raise _unauthorized(detail)

    return await resolve_actor(
        session=session, settings=get_settings(), authorization=authorization
    )


async def get_current_jwt_actor(
    session: SessionDep,
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
) -> ActorContext:
    if authorization is None:
        detail = f"{AUTHORIZATION_HEADER} header is required"
        raise _unauthorized(detail)

    return await resolve_jwt_actor(
        session=session, settings=get_settings(), authorization=authorization
    )


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
