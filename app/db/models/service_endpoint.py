from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import AccessMode
from app.core.json_types import JsonObject
from app.core.service_fields import (
    SERVICE_NAME_MAX_LENGTH,
    SERVICE_SUMMARY_MAX_LENGTH,
    SLUG_MAX_LENGTH,
)
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.endpoint_price import EndpointPrice
    from app.db.models.provider_upstream import ProviderUpstream
    from app.db.models.service import Service


class ServiceEndpoint(Base):
    __tablename__ = "service_endpoints"
    __table_args__ = (UniqueConstraint("service_id", "key"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    service_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("services.id", ondelete="CASCADE"),
        index=True,
    )
    key: Mapped[str] = mapped_column(String(SLUG_MAX_LENGTH))
    name: Mapped[str] = mapped_column(String(SERVICE_NAME_MAX_LENGTH))
    summary: Mapped[str | None] = mapped_column(String(SERVICE_SUMMARY_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(Text)
    access_mode: Mapped[AccessMode] = mapped_column(
        SqlEnum(
            AccessMode,
            name="access_mode",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    request_schema: Mapped[JsonObject] = mapped_column(JSONB)
    response_schema: Mapped[JsonObject] = mapped_column(JSONB)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=datetime.now,
    )

    service: Mapped[Service] = relationship(back_populates="endpoints")
    upstream: Mapped[ProviderUpstream | None] = relationship(
        back_populates="endpoint",
        cascade="all, delete-orphan",
        uselist=False,
    )
    price: Mapped[EndpointPrice | None] = relationship(
        back_populates="endpoint",
        cascade="all, delete-orphan",
        uselist=False,
    )
