from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProviderSigningSecret(Base):
    """The marketplace-issued secret that signs requests to one provider's upstreams.

    One row per provider account. After a rotation the previous secret keeps signing
    beside the new one until `previous_expires_at`, so the provider can deploy the new
    secret without rejecting requests in between; rotating again replaces it. Secrets
    are stored only as Fernet ciphertexts (see `app/services/provider_signing_secrets.py`).
    """

    __tablename__ = "provider_signing_secrets"
    __table_args__ = (
        CheckConstraint(
            "(previous_ciphertext IS NULL) = (previous_expires_at IS NULL)",
            name="previous_secret_complete",
        ),
    )

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
    # When the current secret was issued: at creation, then at every rotation.
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    previous_ciphertext: Mapped[str | None] = mapped_column(Text)
    previous_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
