from pydantic import BaseModel, ConfigDict

from app.schemas.common import DisplayName, Id, Timestamp


class ProviderProfileCreateRequest(BaseModel):
    display_name: DisplayName


class ProviderProfileUpdateRequest(BaseModel):
    display_name: DisplayName | None = None


class ProviderProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: Id
    display_name: DisplayName
    created_at: Timestamp
