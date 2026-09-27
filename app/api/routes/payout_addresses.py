from datetime import UTC, datetime

from fastapi import APIRouter, status

from app.api.deps.auth import CurrentJwtActor
from app.api.deps.database import SessionDep
from app.api.deps.settings import SettingsDep
from app.schemas.payout_address import (
    PayoutAddressChallengeRequest,
    PayoutAddressChallengeResponse,
    PayoutAddressProofRequest,
    PayoutAddressResponse,
)
from app.services import payout_addresses

# JWT only, like the signing secret routes: an API key cannot redirect earnings.
router = APIRouter(prefix="/provider/payout-address", tags=["provider-payouts"])


@router.post(
    "/challenge",
    response_model=PayoutAddressChallengeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a payout address challenge",
    description=(
        "Issues the EIP-712 typed data that proves the provider controls `address` on "
        "`network`, the marketplace's payment network. Sign it with that address's key "
        "(`eth_signTypedData_v4`) before `expires_at` and submit the signature to "
        "`POST /v1/provider/payout-address`. A new request replaces a pending challenge."
    ),
    responses={
        201: {"description": "Challenge issued."},
        403: {"description": "A non-JWT bearer token was supplied."},
        422: {"description": "The address or the network was invalid."},
    },
)
async def request_payout_address_challenge(
    request: PayoutAddressChallengeRequest,
    actor: CurrentJwtActor,
    session: SessionDep,
    settings: SettingsDep,
) -> PayoutAddressChallengeResponse:
    challenge = await payout_addresses.request_payout_address_challenge(
        session=session,
        settings=settings,
        account_id=actor.account_id,
        address=request.address,
        network=request.network,
    )
    return PayoutAddressChallengeResponse(
        nonce=challenge.nonce,
        expires_at=challenge.expires_at,
        typed_data=payout_addresses.proof_typed_data(challenge),
    )


@router.post(
    "",
    response_model=PayoutAddressResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Prove a payout address",
    description=(
        "Records the pending challenge's address as the provider's payout address, given "
        "that address's signature of the challenge. It replaces any earlier one at once, "
        "and payouts are held until its `effective_at`."
    ),
    responses={
        201: {"description": "Payout address recorded; payouts are held until it is effective."},
        403: {"description": "A non-JWT bearer token was supplied."},
        409: {"description": "No challenge is pending, or it has expired."},
        422: {"description": "The signature is not the address's signature of the challenge."},
    },
)
async def prove_payout_address(
    request: PayoutAddressProofRequest,
    actor: CurrentJwtActor,
    session: SessionDep,
    settings: SettingsDep,
) -> PayoutAddressResponse:
    proven = await payout_addresses.prove_payout_address(
        session=session,
        settings=settings,
        account_id=actor.account_id,
        signature=request.signature,
    )
    return PayoutAddressResponse.from_model(proven, now=datetime.now(UTC))


@router.get(
    "",
    response_model=PayoutAddressResponse,
    summary="Get the provider's payout address",
    description=(
        "Returns the provider's latest proven payout address on the payment network: "
        "`pending` while payouts are held, until `effective_at`, then `effective`."
    ),
    responses={
        200: {"description": "Payout address returned."},
        403: {"description": "A non-JWT bearer token was supplied."},
        404: {"description": "The provider has proven no payout address."},
    },
)
async def get_payout_address(
    actor: CurrentJwtActor,
    session: SessionDep,
    settings: SettingsDep,
) -> PayoutAddressResponse:
    latest = await payout_addresses.get_payout_address(
        session=session,
        settings=settings,
        account_id=actor.account_id,
    )
    return PayoutAddressResponse.from_model(latest, now=datetime.now(UTC))
