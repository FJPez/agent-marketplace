"""Path dependency that parses the public service identifier."""

from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.schemas.service_ref import PublicServiceRef, parse_public_service_ref


def require_public_service_ref(service_id_or_slug: str) -> PublicServiceRef:
    try:
        return parse_public_service_ref(service_id_or_slug)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="service identifier must be a service id or a service slug",
        ) from exc


ServiceRefPath = Annotated[PublicServiceRef, Depends(require_public_service_ref)]
