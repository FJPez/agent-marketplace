"""The global rate limit, counted per account or, for other callers, per client address."""

from typing import Annotated

from fastapi import Depends, Request

from app.api.deps.auth import OptionalActor
from app.api.deps.settings import SettingsDep
from app.core.errors import RateLimitedError
from app.core.lifespan import get_resources
from app.core.rate_limits_backend import RateLimitsBackend

GLOBAL_SCOPE = "global"


def get_rate_limits_backend(request: Request) -> RateLimitsBackend:
    return get_resources(request).rate_limits_backend


RateLimitsBackendDep = Annotated[RateLimitsBackend, Depends(get_rate_limits_backend)]


async def enforce_rate_limit(
    request: Request,
    actor: OptionalActor,
    settings: SettingsDep,
    backend: RateLimitsBackendDep,
) -> None:
    limit = settings.api_rate_limit

    if actor is None:
        client_host = request.client.host if request.client is not None else "unknown"
        key = f"client:{client_host}"
    else:
        key = f"account:{actor.account_id}"

    if await backend.hit(limit, key=key, scope=GLOBAL_SCOPE):
        return
    retry_after = await backend.seconds_until_reset(limit, key=key, scope=GLOBAL_SCOPE)
    raise RateLimitedError("rate limit exceeded", headers={"Retry-After": str(retry_after)})
