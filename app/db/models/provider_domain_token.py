from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProviderDomainToken(Base):
    """The token a provider publishes in DNS to prove it controls its upstream hosts.

    One per provider account, created on first request and never changed: it is public
    once published, and proves nothing on its own. Publishing checks that every
    upstream host carries it (`app/services/domain_control.py`).
    """

    __tablename__ = "provider_domain_tokens"

    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "accounts.id",
            name="fk_provider_domain_tokens_account_id_accounts",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
