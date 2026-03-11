"""API dependencies package."""

from app.api.deps.auth import CurrentActor, get_current_actor

__all__ = ["CurrentActor", "get_current_actor"]
