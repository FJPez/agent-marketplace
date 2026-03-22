from typing import Annotated

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.quote import QuoteCreateRequest, QuoteResponse
from app.services.quote_service import QuoteService

router = APIRouter(tags=["quotes"])

QUOTE_ROUTE_DESCRIPTION = (
    "Creates a quote for a priced endpoint using the exact request payload that will later "
    "be invoked. This route is publicly accessible, but quotes are still bound to the "
    "service revision, change token, request hash, and expiry window."
)


@router.post(
    "/services/{service_id_or_slug}/quote",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a quote for a priced endpoint",
    description=QUOTE_ROUTE_DESCRIPTION,
    responses={
        201: {"description": "Quote created successfully."},
        404: {"description": "The requested public service or endpoint does not exist."},
        409: {"description": "The endpoint is unavailable for quoting in its current state."},
    },
)
async def create_quote(
    service_id_or_slug: str,
    request: Annotated[
        QuoteCreateRequest,
        Body(
            openapi_examples={
                "paid-summary": {
                    "summary": "Create a quote for the paid demo endpoint",
                    "value": {
                        "endpoint_key": "paid-summary",
                        "payload": {
                            "message": "Summarize the request after the payment flow completes."
                        },
                    },
                }
            }
        ),
    ],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> QuoteResponse:
    service = QuoteService(session)
    quote = await service.create_quote(
        service_id_or_slug=service_id_or_slug,
        endpoint_key=request.endpoint_key,
        payload=request.payload,
    )
    return QuoteResponse.from_model(quote)
