from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, String, Text, text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ServiceLifecycle
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.service_endpoint import ServiceEndpoint
    from app.db.models.service_tag import ServiceTag


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    provider_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("provider_profiles.account_id", ondelete="CASCADE"),
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    lifecycle: Mapped[ServiceLifecycle] = mapped_column(
        SqlEnum(
            ServiceLifecycle,
            name="service_lifecycle",
            native_enum=False,
            values_callable=lambda values: [value.value for value in values],
        ),
        default=ServiceLifecycle.DRAFT,
        server_default=ServiceLifecycle.DRAFT.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=datetime.now,
    )

    tags: Mapped[list[ServiceTag]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
    )
    endpoints: Mapped[list[ServiceEndpoint]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
    )


__all__ = ["Service"]
