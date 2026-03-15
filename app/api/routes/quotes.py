from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import OptionalCurrentActor
from app.db.session import get_db_session
from app.schemas.quote import QuoteCreateRequest, QuoteResponse
from app.services.quote_service import QuoteNotFoundError, QuoteService

router = APIRouter(tags=["quotes"])


def _to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, QuoteNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="internal server error",
    )


@router.post(
    "/services/{service_id_or_slug}/quote",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_quote(
    service_id_or_slug: str,
    request: QuoteCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    actor: OptionalCurrentActor = None,
) -> QuoteResponse:
    service = QuoteService(session)
    try:
        quote = await service.create_quote(
            service_id_or_slug=service_id_or_slug,
            endpoint_key=request.endpoint_key,
            payload=request.payload,
        )
    except QuoteNotFoundError as exc:
        raise _to_http_exception(exc) from exc

    return QuoteResponse.from_model(quote)
