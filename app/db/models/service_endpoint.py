from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import AccessMode
from app.core.json_types import JsonObject
from app.core.service_fields import (
    DEFAULT_RESPONSE_CONTENT_TYPE,
    ENDPOINT_TIMEOUT_MAX_SECONDS,
    SERVICE_NAME_MAX_LENGTH,
    SERVICE_SUMMARY_MAX_LENGTH,
    SLUG_MAX_LENGTH,
)
from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.db.models.listing_price import ListingPrice
    from app.db.models.provider_upstream import ProviderUpstream
    from app.db.models.service import Service


class ServiceEndpoint(Base):
    __tablename__ = "service_endpoints"
    __table_args__ = (
        UniqueConstraint("service_id", "key"),
        CheckConstraint(
            "jsonb_typeof(request_schema) = 'object'",
            name="request_schema_json_object",
        ),
        CheckConstraint(
            "jsonb_typeof(response_schema) = 'object'",
            name="response_schema_json_object",
        ),
        CheckConstraint(
            f"timeout_seconds BETWEEN 1 AND {ENDPOINT_TIMEOUT_MAX_SECONDS}",
            name="timeout_seconds_range",
        ),
        # A paid endpoint may lack a price while its service is a draft (publish
        # readiness requires one); a free endpoint never has one.
        CheckConstraint(
            "access_mode = 'paid' OR current_price_id IS NULL",
            name="free_has_no_price",
        ),
        # The current price is one of this endpoint's own versions. A null
        # current_price_id skips the check (MATCH SIMPLE). use_alter:
        # listing_prices also references service_endpoints.
        ForeignKeyConstraint(
            ["id", "current_price_id"],
            ["listing_prices.endpoint_id", "listing_prices.id"],
            name="fk_service_endpoints_id_current_price_id_listing_prices",
            use_alter=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    service_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("services.id", ondelete="CASCADE"),
        index=True,
    )
    key: Mapped[str] = mapped_column(String(SLUG_MAX_LENGTH))
    name: Mapped[str] = mapped_column(String(SERVICE_NAME_MAX_LENGTH))
    summary: Mapped[str | None] = mapped_column(String(SERVICE_SUMMARY_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(Text)
    access_mode: Mapped[AccessMode] = mapped_column(
        SqlEnum(
            AccessMode,
            name="access_mode",
            create_constraint=True,
            native_enum=False,
            values_callable=lambda values: [value.value for value in values],
        ),
    )
    request_schema: Mapped[JsonObject] = mapped_column(JSONB)
    response_schema: Mapped[JsonObject] = mapped_column(JSONB)
    # A media type is at most 127 + 1 + 127 characters (MEDIA_TYPE_PATTERN).
    response_content_type: Mapped[str] = mapped_column(
        String(255),
        server_default=DEFAULT_RESPONSE_CONTENT_TYPE,
    )
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    # Provider-declared: a request re-sent with the same Idempotency-Key is safe.
    supports_idempotency: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    current_price_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=utc_now,
    )

    service: Mapped[Service] = relationship(back_populates="endpoints")
    upstream: Mapped[ProviderUpstream | None] = relationship(
        back_populates="endpoint",
        cascade="all, delete-orphan",
        uselist=False,
    )
    # The composite key above also names `id`, so the join and its foreign key
    # are spelled out.
    current_price: Mapped[ListingPrice | None] = relationship(
        primaryjoin="ServiceEndpoint.current_price_id == ListingPrice.id",
        foreign_keys=[current_price_id],
    )
