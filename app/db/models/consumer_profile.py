from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ConsumerProfile(Base):
    __tablename__ = "consumer_profiles"

    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
