from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import PaymentAttemptStatus
from app.db.base import Base


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint("payment_identifier"),
        CheckConstraint(
            "status = 'settling' OR settle_in_progress_until IS NULL",
            name="lease_only_settling",
        ),
        # One caller's one request has at most one payment that is still worth money:
        # only the two definitively rejected payments may be replaced by another.
        Index(
            "uq_payment_attempts_active_request",
            "consumer_account_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("status NOT IN ('verify_failed', 'settle_failed')"),
        ),
    )

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
    status: Mapped[PaymentAttemptStatus] = mapped_column(
        SqlEnum(
            PaymentAttemptStatus,
            name="payment_attempt_status",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda values: [value.value for value in values],
        ),
        server_default=PaymentAttemptStatus.CHALLENGED.value,
    )
    payment_requirement: Mapped[dict[str, object]] = mapped_column(JSONB)
    payment_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    verify_outcome: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    settle_outcome: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    facilitator_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    settle_in_progress_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=text("now()"),
    )
