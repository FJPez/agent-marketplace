"""Marketplace-issued signing secrets for provider accounts.

The provider proxy (phase 1) signs every request to a provider's upstreams with the
provider's signing secret, so the provider can check the request came from the
marketplace. The marketplace generates the secret, returns it once, and stores it
only encrypted under `provider_secret_encryption_keys`.
"""

from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidStateError, NotFoundError
from app.core.logging import get_logger
from app.db.models import ProviderSigningSecret

logger = get_logger(__name__)


async def create_signing_secret(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
) -> tuple[ProviderSigningSecret, str]:
    """Issue the account's first signing secret; return its row and the plaintext."""
    secret, ciphertext = _issue(_cipher(settings))
    created = await session.scalar(
        insert(ProviderSigningSecret)
        .values(account_id=account_id, ciphertext=ciphertext, issued_at=datetime.now(UTC))
        # A concurrent create inserts nothing here instead of raising.
        .on_conflict_do_nothing(index_elements=[ProviderSigningSecret.account_id])
        .returning(ProviderSigningSecret),
    )
    if created is None:
        raise ConflictError("the account already has a signing secret; rotate it instead")
    await session.commit()
    return created, secret


async def rotate_signing_secret(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
) -> tuple[ProviderSigningSecret, str]:
    """Replace the current secret; it keeps signing for the grace window.

    A previous secret still in its grace window is dropped: at most two secrets sign.
    """
    cipher = _cipher(settings)
    stored = await session.scalar(
        select(ProviderSigningSecret)
        .where(ProviderSigningSecret.account_id == account_id)
        .with_for_update(),
    )
    if stored is None:
        raise NotFoundError("the account has no signing secret; create one first")

    secret, ciphertext = _issue(cipher)
    now = datetime.now(UTC)
    stored.previous_ciphertext = stored.ciphertext
    stored.previous_expires_at = now + timedelta(seconds=settings.provider_secret_grace_seconds)
    stored.ciphertext = ciphertext
    stored.issued_at = now
    await session.commit()
    return stored, secret


async def get_signing_secret(*, session: AsyncSession, account_id: int) -> ProviderSigningSecret:
    stored = await session.get(ProviderSigningSecret, account_id)
    if stored is None:
        raise NotFoundError("the account has no signing secret")
    return stored


async def load_signing_secrets(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
) -> list[str]:
    """Every secret a request to the provider is signed with now, the current one first.

    The current secret must decrypt. A previous secret that no longer does (its key was
    removed from the list) is skipped, as the grace for it cannot be honoured anyway.
    """
    cipher = _cipher(settings)
    stored = await session.get(ProviderSigningSecret, account_id)
    if stored is None:
        raise InvalidStateError("the provider has no signing secret")

    signing = [_decrypt(cipher, stored.ciphertext)]
    if (
        stored.previous_ciphertext is not None
        and stored.previous_expires_at is not None
        and stored.previous_expires_at > datetime.now(UTC)
    ):
        try:
            signing.append(cipher.decrypt(stored.previous_ciphertext).decode())
        except InvalidToken:
            # Logged without the ciphertext, which is the secret, encrypted.
            logger.warning(
                "previous signing secret cannot be decrypted; it no longer signs",
                extra={"account_id": account_id},
            )
    return signing


def _cipher(settings: Settings) -> MultiFernet:
    if not settings.provider_secret_encryption_keys:
        raise InvalidStateError(
            "provider signing secrets are unavailable until "
            "APP_PROVIDER_SECRET_ENCRYPTION_KEYS is configured",
        )
    # MultiFernet encrypts with the first key and decrypts with any of them.
    return MultiFernet(
        [Fernet(key.get_secret_value()) for key in settings.provider_secret_encryption_keys],
    )


def _issue(cipher: MultiFernet) -> tuple[str, str]:
    """A new secret and its ciphertext.

    The secret is 32 random bytes; its prefix tells it apart from an API key.
    """
    secret = f"amp_sig_{token_urlsafe(32)}"
    return secret, cipher.encrypt(secret.encode()).decode()


def _decrypt(cipher: MultiFernet, ciphertext: str) -> str:
    try:
        return cipher.decrypt(ciphertext).decode()
    except InvalidToken as exc:
        raise InvalidStateError(
            "the provider's signing secret cannot be decrypted with "
            "APP_PROVIDER_SECRET_ENCRYPTION_KEYS",
        ) from exc
