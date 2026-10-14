from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import AtomicAmount

# The editing service reads a violation of this key as a concurrent price change.
LISTING_PRICE_VERSION_CONSTRAINT = "uq_listing_prices_endpoint_id_version"


class ListingPrice(Base):
    """One price version of a paid endpoint; a database trigger refuses any update."""

    __tablename__ = "listing_prices"
    __table_args__ = (
        UniqueConstraint("endpoint_id", "version", name=LISTING_PRICE_VERSION_CONSTRAINT),
        # The target of ServiceEndpoint's composite current-price key.
        UniqueConstraint("endpoint_id", "id", name="uq_listing_prices_endpoint_id_id"),
        CheckConstraint("version > 0", name="positive_version"),
        CheckConstraint("amount > 0", name="positive_amount"),
        CheckConstraint("max_timeout_seconds > 0", name="positive_max_timeout_seconds"),
        CheckConstraint("fee_bps BETWEEN 0 AND 10000", name="fee_bps_range"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "service_endpoints.id",
            name="fk_listing_prices_endpoint_id_service_endpoints",
            ondelete="CASCADE",
        ),
    )
    version: Mapped[int] = mapped_column(Integer)
    # Atomic units of `asset` (1 USDC = 1,000,000).
    amount: Mapped[int] = mapped_column(AtomicAmount)
    asset: Mapped[str] = mapped_column(String(42))
    # A CAIP-2 chain id such as eip155:84532: at most 8 + 1 + 32 characters.
    network: Mapped[str] = mapped_column(String(41))
    pay_to: Mapped[str] = mapped_column(String(42))
    max_timeout_seconds: Mapped[int] = mapped_column(Integer)
    fee_bps: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
