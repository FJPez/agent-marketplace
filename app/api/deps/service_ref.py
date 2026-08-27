"""Path annotation that validates the public service identifier."""

from typing import Annotated

from fastapi import Path

from app.schemas.service_ref import PublicServiceRef

ServiceRefPath = Annotated[PublicServiceRef, Path(alias="service_id_or_slug")]
