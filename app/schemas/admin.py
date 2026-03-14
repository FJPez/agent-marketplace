from typing import Self

from pydantic import BaseModel, StringConstraints

from app.db.models import ModerationAction
from app.schemas.common import Id, Timestamp

Reason = StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)


class ModerationActionRequest(BaseModel):
    reason: str


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
