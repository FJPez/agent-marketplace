from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProviderSigningSecret(Base):
    """A provider's signing secret, stored only as a Fernet ciphertext."""

    __tablename__ = "provider_signing_secrets"

    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "accounts.id",
            name="fk_provider_signing_secrets_account_id_accounts",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    ciphertext: Mapped[str] = mapped_column(Text)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
