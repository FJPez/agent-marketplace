from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, Field, StringConstraints

from app.core.json_types import JsonObject as CoreJsonObject
from app.core.json_types import JsonValue as CoreJsonValue
from app.core.security import checksum_address

Id = Annotated[int, Field(strict=True, gt=0)]
Timestamp = AwareDatetime
WalletAddress = Annotated[str, AfterValidator(checksum_address)]
DisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
JsonValue = CoreJsonValue
JsonObject = CoreJsonObject


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ServiceEntrypointResponse(BaseModel):
    name: str
    status: Literal["ok"]
    docs: str
    health: str
    ready: str
