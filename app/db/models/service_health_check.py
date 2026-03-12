from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, Identity, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ServiceHealthStatus
from app.db.base import Base

SERVICE_HEALTH_STATUS_ENUM = Enum(
    ServiceHealthStatus,
    native_enum=False,
    values_callable=lambda enum_cls: [item.value for item in enum_cls],
    length=16,
)


class ServiceHealthCheck(Base):
    """Service health records use a scalar service_id until provider services land."""

    __tablename__ = "service_health_checks"
    __table_args__ = (
        Index(
            "ix_service_health_checks_service_id_check_name_checked_at",
            "service_id",
            "check_name",
            "checked_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    service_id: Mapped[int] = mapped_column(BigInteger)
    check_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[ServiceHealthStatus] = mapped_column(SERVICE_HEALTH_STATUS_ENUM)
    summary: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
