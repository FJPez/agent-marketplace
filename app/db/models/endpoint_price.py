from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.service_endpoint import ServiceEndpoint


class EndpointPrice(Base):
    __tablename__ = "endpoint_prices"
    __table_args__ = (
        CheckConstraint(
            "amount_minor > 0",
            name="positive_amount_minor",
        ),
    )

    endpoint_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("service_endpoints.id", ondelete="CASCADE"),
        primary_key=True,
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=datetime.now,
    )

    endpoint: Mapped[ServiceEndpoint] = relationship(back_populates="pricing")
