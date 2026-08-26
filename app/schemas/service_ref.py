"""Public service identifier shared by the discovery and quote APIs."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.service import Slug

SERVICE_ID_MAX = 9223372036854775807

ServiceId = Annotated[int, Field(gt=0, le=SERVICE_ID_MAX)]


class PublicServiceRef(BaseModel):
    """A public service identifier: exactly one of a service id or a service slug."""

    model_config = ConfigDict(frozen=True)

    id: ServiceId | None = None
    slug: Slug | None = None

    @model_validator(mode="after")
    def check_exactly_one_identifier(self) -> Self:
        if (self.id is None) == (self.slug is None):
            msg = "service reference must carry either an id or a slug"
            raise ValueError(msg)
        return self


def parse_public_service_ref(value: str) -> PublicServiceRef:
    """Purely numeric identifiers address the service id; anything else must be a slug."""
    if value.isdigit():
        return PublicServiceRef(id=int(value))
    return PublicServiceRef(slug=value)
