from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, String, Text, text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import PayoutFailureCode, PayoutStatus
from app.db.base import Base


class Payout(Base):
    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    provider_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("services.id", ondelete="CASCADE"),
        index=True,
    )
    invocation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("invocations.id", ondelete="CASCADE"),
        index=True,
    )
    payment_attempt_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("payment_attempts.id", ondelete="CASCADE"),
        unique=True,
    )
    destination_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(16))
    network: Mapped[str] = mapped_column(String(64))
    status: Mapped[PayoutStatus] = mapped_column(
        SqlEnum(
            PayoutStatus,
            name="payout_status",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda values: [value.value for value in values],
        ),
        index=True,
    )
    transfer_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_idempotency_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    failure_code: Mapped[PayoutFailureCode | None] = mapped_column(
        SqlEnum(
            PayoutFailureCode,
            name="payout_failure_code",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    prepared_raw_transaction: Mapped[str | None] = mapped_column(Text, nullable=True)
    chain_nonce: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=text("now()"),
    )
