from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.db.session import get_db_session
from app.repositories.account_repo import AccountRepository

X_ACCOUNT_ID_HEADER = "X-Account-Id"
_INVALID_ACCOUNT_ID_DETAIL = f"{X_ACCOUNT_ID_HEADER} must be a positive integer"


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _parse_account_id(x_account_id: str) -> int:
    try:
        account_id = int(x_account_id)
    except ValueError as exc:
        raise _unauthorized(_INVALID_ACCOUNT_ID_DETAIL) from exc

    if account_id <= 0:
        raise _unauthorized(_INVALID_ACCOUNT_ID_DETAIL)

    return account_id


async def _build_actor_context(
    session: AsyncSession,
    *,
    x_account_id: str,
) -> ActorContext:
    account_id = _parse_account_id(x_account_id)
    account_repo = AccountRepository(session)
    account = await account_repo.get(account_id)
    if account is None:
        raise _unauthorized("authenticated account does not exist")

    return ActorContext(account_id=account.id, is_admin=account.is_admin)


async def get_optional_current_actor(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    x_account_id: Annotated[str | None, Header(alias=X_ACCOUNT_ID_HEADER)] = None,
) -> ActorContext | None:
    if x_account_id is None:
        return None

    return await _build_actor_context(session, x_account_id=x_account_id)


async def get_current_actor(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    x_account_id: Annotated[str | None, Header(alias=X_ACCOUNT_ID_HEADER)] = None,
) -> ActorContext:
    if x_account_id is None:
        detail = f"{X_ACCOUNT_ID_HEADER} header is required"
        raise _unauthorized(detail)

    return await _build_actor_context(session, x_account_id=x_account_id)


async def get_admin_actor(
    actor: Annotated[ActorContext, Depends(get_current_actor)],
) -> ActorContext:
    if not actor.is_admin:
        raise _forbidden("admin privileges required")
    return actor


CurrentActor = Annotated[ActorContext, Depends(get_current_actor)]
OptionalCurrentActor = Annotated[ActorContext | None, Depends(get_optional_current_actor)]
AdminActor = Annotated[ActorContext, Depends(get_admin_actor)]
