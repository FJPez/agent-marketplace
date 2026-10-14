"""The global rate limit, counted per account or, for other callers, per client address."""

from fastapi import Request

from app.api.deps.auth import OptionalActor
from app.api.deps.settings import SettingsDep
from app.core.errors import RateLimitedError
from app.core.lifespan import get_resources

GLOBAL_SCOPE = "global"


async def enforce_rate_limit(
    request: Request,
    actor: OptionalActor,
    settings: SettingsDep,
) -> None:
    limit = settings.api_rate_limit
    backend = get_resources(request.app).rate_limits_backend

    if actor is None:
        client_host = request.client.host if request.client is not None else "unknown"
        key = f"client:{client_host}"
    else:
        key = f"account:{actor.account_id}"

    if await backend.hit(limit, key=key, scope=GLOBAL_SCOPE):
        return
    retry_after = await backend.seconds_until_reset(limit, key=key, scope=GLOBAL_SCOPE)
    raise RateLimitedError("rate limit exceeded", headers={"Retry-After": str(retry_after)})
