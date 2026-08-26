from typing import Annotated

from fastapi import APIRouter, Body, status

from app.api.deps.database import SessionDep
from app.api.deps.service_ref import ServiceRefPath
from app.api.deps.settings import SettingsDep
from app.schemas.quote import QuoteCreateRequest, QuoteResponse
from app.services import quotes

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
        422: {
            "description": (
                "The supplied identifier was neither a service id nor a slug, or the payload "
                "did not match the endpoint request schema."
            )
        },
    },
)
async def create_quote(
    service_ref: ServiceRefPath,
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
    session: SessionDep,
    settings: SettingsDep,
) -> QuoteResponse:
    quote = await quotes.create_quote(
        session=session,
        settings=settings,
        service_ref=service_ref,
        request=request,
    )
    return QuoteResponse.from_model(quote)
