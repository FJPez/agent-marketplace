from __future__ import annotations

from typing import TYPE_CHECKING

from coredis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import UnavailableError
from app.schemas.common import HealthResponse

if TYPE_CHECKING:
    from app.core.resources import Resources


class ReadinessCheckError(UnavailableError):
    default_problem_type = "not_ready"


def get_health_response() -> HealthResponse:
    return HealthResponse(status="ok")


async def get_readiness_response(resources: Resources) -> HealthResponse:
    try:
        async with resources.db_session_factory() as session:
            await session.execute(text("SELECT 1"))
    # asyncpg raises a refused or timed-out connection as a plain OSError, not wrapped.
    except (SQLAlchemyError, OSError) as exc:
        raise ReadinessCheckError("database unavailable") from exc

    if resources.redis_client is not None:
        try:
            await resources.redis_client.ping()
        except RedisError as exc:
            raise ReadinessCheckError("redis unavailable") from exc

    return HealthResponse(status="ok")
