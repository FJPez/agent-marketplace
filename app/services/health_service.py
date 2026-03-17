from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.common import HealthResponse

if TYPE_CHECKING:
    from app.core.lifespan import AppState


class ReadinessCheckError(RuntimeError):
    pass


def get_health_response() -> HealthResponse:
    return HealthResponse(status="ok")


async def get_readiness_response(app_state: AppState) -> HealthResponse:
    session_factory = app_state.db_session_factory
    if session_factory is None:
        raise ReadinessCheckError("database unavailable")

    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise ReadinessCheckError("database unavailable") from exc

    return HealthResponse(status="ok")
