from pydantic import BaseModel, ConfigDict

from app.schemas.common import Timestamp


class SigningSecretResponse(BaseModel):
    """When the provider's signing secret was issued; never the secret itself."""

    model_config = ConfigDict(from_attributes=True)

    issued_at: Timestamp


class IssuedSigningSecretResponse(SigningSecretResponse):
    secret: str


class DomainVerificationResponse(BaseModel):
    """The TXT record to publish at `<label>.<host>` for every upstream host."""

    model_config = ConfigDict(from_attributes=True)

    label: str
    value: str
