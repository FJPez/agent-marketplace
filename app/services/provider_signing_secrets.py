"""Signing secrets the provider proxy (phase 1) signs each request to a provider with."""

from datetime import UTC, datetime
from secrets import token_urlsafe

from cryptography.fernet import Fernet
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.db.models import ProviderSigningSecret


async def issue_signing_secret(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
) -> tuple[ProviderSigningSecret, str]:
    """Issue a new secret, replacing any earlier one; return its row and the plaintext."""
    # The prefix tells it apart from an API key.
    secret = f"amp_sig_{token_urlsafe(32)}"
    fernet = Fernet(settings.provider_secret_encryption_key.get_secret_value())
    values = {
        "ciphertext": fernet.encrypt(secret.encode()).decode(),
        "issued_at": datetime.now(UTC),
    }
    issued = (
        await session.execute(
            insert(ProviderSigningSecret)
            .values(account_id=account_id, **values)
            .on_conflict_do_update(index_elements=[ProviderSigningSecret.account_id], set_=values)
            .returning(ProviderSigningSecret),
        )
    ).scalar_one()
    await session.commit()
    return issued, secret


async def get_signing_secret(*, session: AsyncSession, account_id: int) -> ProviderSigningSecret:
    stored = await session.get(ProviderSigningSecret, account_id)
    if stored is None:
        raise NotFoundError("the account has no signing secret")
    return stored
