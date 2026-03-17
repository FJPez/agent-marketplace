from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.db.models import ModerationAction
from app.schemas.common import Id, Timestamp

Reason = StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)


class ModerationActionRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"reason": "Endpoint is unavailable during provider maintenance."}]
        }
    )

    reason: Annotated[str, Reason]


class ModerationActionResponse(BaseModel):
    id: Id
    service_id: Id
    actor_account_id: Id | None
    action: str
    reason: str
    created_at: Timestamp

    @classmethod
    def from_model(cls, action: ModerationAction) -> Self:
        return cls(
            id=action.id,
            service_id=action.service_id,
            actor_account_id=action.actor_account_id,
            action=action.action,
            reason=action.reason,
            created_at=action.created_at,
        )
