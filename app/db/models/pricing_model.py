from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import PricingModelType
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.service_endpoint import ServiceEndpoint


class PricingModel(Base):
    __tablename__ = "pricing_models"
    __table_args__ = (
        CheckConstraint(
            (
                "(pricing_type = 'free' AND amount_minor IS NULL AND currency IS NULL) OR "
                "(pricing_type = 'fixed_per_call' AND amount_minor IS NOT NULL "
                "AND currency IS NOT NULL)"
            ),
            name="pricing_shape",
        ),
        CheckConstraint(
            "amount_minor IS NULL OR amount_minor > 0",
            name="positive_amount_minor",
        ),
    )

    endpoint_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("service_endpoints.id", ondelete="CASCADE"),
        primary_key=True,
    )
    pricing_type: Mapped[PricingModelType] = mapped_column(
        SqlEnum(
            PricingModelType,
            name="pricing_model_type",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
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
