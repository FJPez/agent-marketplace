"""Public service identifier shared by the discovery and quote APIs."""

from typing import Annotated

from pydantic import Field

from app.schemas.service import Slug

SERVICE_ID_MAX = 9223372036854775807

ServiceId = Annotated[int, Field(gt=0, le=SERVICE_ID_MAX)]

# Left-to-right makes the no-fallback contract structural: a purely numeric identifier can only
# ever be a service id, because Slug rejects digits-only strings, so it fails validation outright
# rather than falling back to a slug lookup.
PublicServiceRef = Annotated[ServiceId | Slug, Field(union_mode="left_to_right")]
