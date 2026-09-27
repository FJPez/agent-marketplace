from pydantic import BaseModel, ConfigDict

from app.schemas.common import Timestamp


class SigningSecretResponse(BaseModel):
    """A provider's signing secret status; never the secret itself.

    `previous_expires_at` is when the secret replaced by the last rotation stops
    signing requests (in the past once it has); null before the first rotation.
    """

    model_config = ConfigDict(from_attributes=True)

    issued_at: Timestamp
    previous_expires_at: Timestamp | None


class IssuedSigningSecretResponse(SigningSecretResponse):
    """A newly issued signing secret: the only response that carries it."""

    secret: str
