from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PayoutAddressChallenge(Base):
    """The payout address proof a provider may sign now: one per provider.

    Requesting another replaces it, and a proof consumes it.
    """

    __tablename__ = "payout_address_challenges"

    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "accounts.id",
            name="fk_payout_address_challenges_account_id_accounts",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    network: Mapped[str] = mapped_column(String(41))
    address: Mapped[str] = mapped_column(String(42))
    nonce: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
