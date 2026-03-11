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


async def get_current_actor(
    x_account_id: Annotated[str | None, Header(alias=X_ACCOUNT_ID_HEADER)] = None,
    session: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> ActorContext:
    if x_account_id is None:
        detail = f"{X_ACCOUNT_ID_HEADER} header is required"
        raise _unauthorized(detail)

    try:
        account_id = int(x_account_id)
    except ValueError as exc:
        raise _unauthorized(_INVALID_ACCOUNT_ID_DETAIL) from exc

    if account_id <= 0:
        raise _unauthorized(_INVALID_ACCOUNT_ID_DETAIL)

    account_repo = AccountRepository(session)
    if not await account_repo.exists(account_id):
        raise _unauthorized("authenticated account does not exist")

    return ActorContext(account_id=account_id)


CurrentActor = Annotated[ActorContext, Depends(get_current_actor)]
