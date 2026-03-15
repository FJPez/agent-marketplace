from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (UniqueConstraint("payment_identifier"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    consumer_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
    )
    quote_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quotes.id", ondelete="CASCADE"),
        index=True,
    )
    invocation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("invocations.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255))
    payment_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    payment_requirement: Mapped[dict[str, object]] = mapped_column(JSONB)
    payment_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    verify_outcome: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    settle_outcome: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    facilitator_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
