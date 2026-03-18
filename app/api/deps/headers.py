from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
_MAX_IDEMPOTENCY_KEY_LENGTH = 255


def require_idempotency_key(
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
) -> str:
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Idempotency-Key header must not be blank",
        )
    if len(normalized_key) > _MAX_IDEMPOTENCY_KEY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Idempotency-Key header must be at most 255 characters",
        )
    return normalized_key


ValidatedIdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
