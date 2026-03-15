from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
