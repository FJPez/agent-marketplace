"""Authentication dependencies; they form a chain, so the actor is resolved once per request."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps.database import SessionFactoryDep
from app.api.deps.settings import SettingsDep
from app.core.actor import ActorContext
from app.core.errors import PermissionDeniedError, UnauthenticatedError
from app.services.auth import resolve_actor

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="A JWT access token or an API key. Public routes do not require one.",
)


async def get_optional_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session_factory: SessionFactoryDep,
    settings: SettingsDep,
) -> ActorContext | None:
    """The caller, or None when no valid bearer token was sent."""
    if credentials is None:
        return None

    async with session_factory() as session:
        try:
            return await resolve_actor(
                session=session,
                settings=settings,
                token=credentials.credentials,
            )
        except UnauthenticatedError:
            return None


OptionalActor = Annotated[ActorContext | None, Depends(get_optional_actor)]


async def get_current_actor(actor: OptionalActor) -> ActorContext:
    if actor is None:
        raise UnauthenticatedError("a valid bearer token is required")
    return actor


CurrentActor = Annotated[ActorContext, Depends(get_current_actor)]


async def get_current_jwt_actor(actor: CurrentActor) -> ActorContext:
    if actor.auth_method != "jwt":
        raise PermissionDeniedError("jwt authentication required")
    return actor


async def get_admin_actor(actor: CurrentActor) -> ActorContext:
    if not actor.is_admin:
        raise PermissionDeniedError("admin privileges required")
    return actor


CurrentJwtActor = Annotated[ActorContext, Depends(get_current_jwt_actor)]
AdminActor = Annotated[ActorContext, Depends(get_admin_actor)]
