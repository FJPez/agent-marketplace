from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.core.json_types import JsonObject
    from app.db.models.service_endpoint import ServiceEndpoint
else:
    JsonObject = dict[str, object]


class ProviderUpstream(Base):
    __tablename__ = "provider_upstreams"

    endpoint_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("service_endpoints.id", ondelete="CASCADE"),
        primary_key=True,
    )
    base_url: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    http_method: Mapped[str] = mapped_column(String(16))
    config: Mapped[JsonObject] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=datetime.now,
    )

    endpoint: Mapped[ServiceEndpoint] = relationship(back_populates="upstream")
