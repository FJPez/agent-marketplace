from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

Id = Annotated[int, Field(strict=True, gt=0)]
Timestamp = AwareDatetime
RequestHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
DisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]


class HealthResponse(BaseModel):
    status: Literal["ok"]
