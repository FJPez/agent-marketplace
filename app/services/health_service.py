from __future__ import annotations

from typing import TYPE_CHECKING

from coredis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import UnavailableError
from app.schemas.common import HealthResponse

if TYPE_CHECKING:
    from app.core.lifespan import AppState


def get_health_response() -> HealthResponse:
    return HealthResponse(status="ok")


async def get_readiness_response(app_state: AppState) -> HealthResponse:
    session_factory = app_state.db_session_factory
    if session_factory is None:
        raise UnavailableError("database unavailable")

    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise UnavailableError("database unavailable") from exc

    if app_state.settings.redis_url is not None:
        redis_client = app_state.redis_client
        if redis_client is None:
            raise UnavailableError("redis unavailable")
        try:
            await redis_client.ping()
        except RedisError as exc:
            raise UnavailableError("redis unavailable") from exc

    return HealthResponse(status="ok")
