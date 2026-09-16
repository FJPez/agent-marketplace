from typing import Annotated

from fastapi import Header
from pydantic import StringConstraints

from app.core.http_headers import IDEMPOTENCY_KEY_HEADER

ValidatedIdempotencyKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    Header(alias=IDEMPOTENCY_KEY_HEADER),
]
PaymentSignatureHeader = Annotated[str | None, Header(alias="PAYMENT-SIGNATURE")]
