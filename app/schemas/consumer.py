from pydantic import BaseModel, ConfigDict

from app.schemas.common import DisplayName, Id, Timestamp


class ConsumerProfileCreateRequest(BaseModel):
    display_name: DisplayName


class ConsumerProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: Id
    display_name: DisplayName
    created_at: Timestamp
