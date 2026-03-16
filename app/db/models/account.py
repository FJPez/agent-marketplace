from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    wallet_address: Mapped[str | None] = mapped_column(
        String(42),
        unique=True,
        index=True,
        nullable=True,
    )
    account_type: Mapped[str] = mapped_column(
        String(10),
        default="human",
        server_default=text("'human'"),
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
    )
    display_name: Mapped[str] = mapped_column(
        String(255),
        default="Anonymous",
        server_default=text("'Anonymous'"),
    )
    nonce: Mapped[str] = mapped_column(
        String(64),
        default="",
        server_default=text("''"),
    )
    nonce_issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=text("now()"),
    )
    token_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
    )
    wallet_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=text("now()"),
        onupdate=_utc_now,
    )
