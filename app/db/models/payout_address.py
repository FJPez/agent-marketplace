from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PayoutAddress(Base):
    """A payout address a provider proved it controls, on one network.

    A new proof adds a row, which supersedes the provider's earlier ones at once: payouts
    are held until its `effective_at` (`app/services/payout_addresses.py`). Rows are never
    updated or deleted, except with their account: the database refuses both (triggers
    `payout_addresses_immutable` and `payout_addresses_no_direct_delete`, created by
    migration payout_addresses_0010).
    """

    __tablename__ = "payout_addresses"
    __table_args__ = (
        # The provider's latest proof on a network is the one that counts.
        Index("ix_payout_addresses_account_id_network_id", "account_id", "network", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "accounts.id",
            name="fk_payout_addresses_account_id_accounts",
            ondelete="CASCADE",
        ),
    )
    # A CAIP-2 chain id such as eip155:84532: at most 8 + 1 + 32 characters.
    network: Mapped[str] = mapped_column(String(41))
    address: Mapped[str] = mapped_column(String(42))
    # The proof: the challenge nonce and the address's EIP-712 signature over it.
    nonce: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(String(132))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
