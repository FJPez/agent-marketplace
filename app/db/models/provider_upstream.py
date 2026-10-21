from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.service_fields import HTTP_METHOD_MAX_LENGTH
from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.db.models.service_endpoint import ServiceEndpoint


class ProviderUpstream(Base):
    __tablename__ = "provider_upstreams"

    endpoint_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("service_endpoints.id", ondelete="CASCADE"),
        primary_key=True,
    )
    base_url: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    http_method: Mapped[str] = mapped_column(String(HTTP_METHOD_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=utc_now,
    )

    endpoint: Mapped[ServiceEndpoint] = relationship(back_populates="upstream")
