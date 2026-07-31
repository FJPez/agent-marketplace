from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, String, text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import LedgerEntryType
from app.db.base import Base


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    provider_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id"),
        index=True,
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("services.id"),
        index=True,
    )
    invocation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("invocations.id"),
        index=True,
    )
    payment_attempt_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("payment_attempts.id"),
        index=True,
    )
    entry_type: Mapped[LedgerEntryType] = mapped_column(
        SqlEnum(
            LedgerEntryType,
            name="ledger_entry_type",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
