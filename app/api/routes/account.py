from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentJwtActor
from app.db.session import get_db_session
from app.schemas.account import AccountResponse, AccountUpdateRequest
from app.services.accounts import get_account, update_display_name

router = APIRouter(prefix="/account", tags=["account"])


@router.get(
    "/me",
    response_model=AccountResponse,
    summary="Get the authenticated account",
    description=(
        "Returns the marketplace account tied to the current JWT. This route does not "
        "accept API-key bearer tokens."
    ),
    responses={
        200: {"description": "Authenticated account returned successfully."},
        404: {"description": "The authenticated account no longer exists."},
        403: {"description": "A non-JWT bearer token was supplied."},
    },
)
async def get_account_me(
    actor: CurrentJwtActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AccountResponse:
    account = await get_account(session=session, account_id=actor.account_id)
    return AccountResponse.model_validate(account)


@router.patch(
    "/me",
    response_model=AccountResponse,
    summary="Update the authenticated account",
    description=(
        "Updates account self-service fields for the current JWT-backed account. "
        "At least one mutable field must be supplied."
    ),
    responses={
        200: {"description": "Account updated successfully."},
        403: {"description": "A non-JWT bearer token was supplied."},
        404: {"description": "The authenticated account no longer exists."},
        422: {"description": "The request did not contain a valid account update."},
    },
)
async def patch_account_me(
    request: AccountUpdateRequest,
    actor: CurrentJwtActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AccountResponse:
    if "display_name" not in request.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="at least one field must be provided",
        )
    if request.display_name is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="display_name cannot be null",
        )
    account = await update_display_name(
        session=session,
        account_id=actor.account_id,
        display_name=request.display_name,
    )
    return AccountResponse.model_validate(account)
