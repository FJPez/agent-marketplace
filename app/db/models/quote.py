from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, String, text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import PricingModelType
from app.db.base import Base


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    service_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("services.id", ondelete="CASCADE"),
        index=True,
    )
    endpoint_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("service_endpoints.id", ondelete="CASCADE"),
        index=True,
    )
    endpoint_key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    pricing_type: Mapped[PricingModelType] = mapped_column(
        SqlEnum(
            PricingModelType,
            name="pricing_model_type",
            create_constraint=False,
            native_enum=False,
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    service_revision_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("service_revisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    service_change_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
