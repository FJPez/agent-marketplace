from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AccessMode, InvocationStatus
from app.db.base import Base


class Invocation(Base):
    __tablename__ = "invocations"
    __table_args__ = (UniqueConstraint("consumer_account_id", "idempotency_key"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    consumer_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("services.id", ondelete="CASCADE"),
        index=True,
    )
    endpoint_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("service_endpoints.id", ondelete="CASCADE"),
        index=True,
    )
    endpoint_key: Mapped[str] = mapped_column(String(255))
    access_mode: Mapped[AccessMode] = mapped_column(
        SqlEnum(
            AccessMode,
            name="access_mode",
            create_constraint=False,
            native_enum=False,
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    quote_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("quotes.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[InvocationStatus] = mapped_column(
        SqlEnum(
            InvocationStatus,
            name="invocation_status",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    response_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    upstream_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
