from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WalletChangeLog(Base):
    __tablename__ = "wallet_change_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
    )
    previous_wallet_address: Mapped[str] = mapped_column(String(42))
    new_wallet_address: Mapped[str] = mapped_column(String(42))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
