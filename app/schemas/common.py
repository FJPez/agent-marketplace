from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

Id = Annotated[int, Field(gt=0)]
Timestamp = AwareDatetime
RequestHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class HealthResponse(BaseModel):
    status: Literal["ok"]
