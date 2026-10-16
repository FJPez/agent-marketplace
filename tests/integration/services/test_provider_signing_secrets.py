import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import create_provider_account_record
from tests.fixtures.settings import TEST_PROVIDER_SECRET_ENCRYPTION_KEY, build_service_settings

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidStateError, NotFoundError
from app.db.base import utc_now
from app.db.models import ProviderSigningSecret
from app.services import provider_signing_secrets


def _settings_with_keys(*keys: str) -> Settings:
    return build_service_settings().model_copy(
        update={"provider_secret_encryption_keys": tuple(SecretStr(key) for key in keys)},
    )


async def _stored(
    db_session_factory: async_sessionmaker[AsyncSession],
    account_id: int,
) -> ProviderSigningSecret:
    async with db_session_factory() as session:
        stored = await session.get(ProviderSigningSecret, account_id)
    assert stored is not None
    return stored


async def _ciphertext(
    db_session_factory: async_sessionmaker[AsyncSession],
    account_id: int,
) -> str | None:
    """The account's current ciphertext; None when it has no secret."""
    async with db_session_factory() as session:
        stored = await session.get(ProviderSigningSecret, account_id)
    return None if stored is None else stored.ciphertext


async def _create(db_session_factory: async_sessionmaker[AsyncSession], account_id: int) -> str:
    async with db_session_factory() as session:
        _, secret = await provider_signing_secrets.create_signing_secret(
            session=session,
            settings=build_service_settings(),
            account_id=account_id,
        )
    return secret


async def _rotate(
    db_session_factory: async_sessionmaker[AsyncSession],
    account_id: int,
    *,
    settings: Settings | None = None,
) -> str:
    async with db_session_factory() as session:
        _, secret = await provider_signing_secrets.rotate_signing_secret(
            session=session,
            settings=settings or build_service_settings(),
            account_id=account_id,
        )
    return secret


async def _load(
    db_session_factory: async_sessionmaker[AsyncSession],
    account_id: int,
    *,
    settings: Settings | None = None,
) -> list[str]:
    async with db_session_factory() as session:
        return await provider_signing_secrets.load_signing_secrets(
            session=session,
            settings=settings or build_service_settings(),
            account_id=account_id,
        )


async def test_create_signing_secret_stores_it_encrypted_and_returns_it_once(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        created, secret = await provider_signing_secrets.create_signing_secret(
            session=session,
            settings=build_service_settings(),
            account_id=account_id,
        )

    stored = await _stored(db_session_factory, account_id)
    assert secret.startswith("amp_sig_")
    assert len(secret) == len("amp_sig_") + 43
    assert secret not in stored.ciphertext
    assert Fernet(TEST_PROVIDER_SECRET_ENCRYPTION_KEY).decrypt(stored.ciphertext).decode() == secret
    assert stored.issued_at == created.issued_at
    assert stored.previous_ciphertext is None
    assert stored.previous_expires_at is None
    assert await _load(db_session_factory, account_id) == [secret]


async def test_create_signing_secret_twice_is_a_conflict_and_keeps_the_first(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    first = await _create(db_session_factory, account_id)

    with pytest.raises(ConflictError, match="already has a signing secret; rotate it"):
        await _create(db_session_factory, account_id)

    assert await _load(db_session_factory, account_id) == [first]


async def test_concurrent_creates_issue_exactly_one_secret(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    outcomes = await asyncio.gather(
        _create(db_session_factory, account_id),
        _create(db_session_factory, account_id),
        return_exceptions=True,
    )

    issued = [outcome for outcome in outcomes if isinstance(outcome, str)]
    assert len(issued) == 1
    assert [type(outcome) for outcome in outcomes if not isinstance(outcome, str)] == [
        ConflictError,
    ]
    assert await _load(db_session_factory, account_id) == issued


@pytest.mark.parametrize(
    ("operation", "has_secret"),
    [
        pytest.param(provider_signing_secrets.create_signing_secret, False, id="create"),
        pytest.param(provider_signing_secrets.rotate_signing_secret, True, id="rotate"),
        pytest.param(provider_signing_secrets.load_signing_secrets, True, id="load"),
    ],
)
async def test_signing_secrets_without_an_encryption_key_are_an_invalid_state(
    db_session_factory: async_sessionmaker[AsyncSession],
    operation: Callable[..., Awaitable[object]],
    has_secret: bool,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    if has_secret:
        await _create(db_session_factory, account_id)
    before = await _ciphertext(db_session_factory, account_id)

    async with db_session_factory() as session:
        with pytest.raises(
            InvalidStateError,
            match="unavailable until APP_PROVIDER_SECRET_ENCRYPTION_KEYS is configured",
        ):
            await operation(
                session=session,
                settings=_settings_with_keys(),
                account_id=account_id,
            )

    assert await _ciphertext(db_session_factory, account_id) == before


async def test_a_secret_in_use_before_a_rotation_keeps_signing_during_the_grace_window(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    first = await _create(db_session_factory, account_id)
    in_flight = await _load(db_session_factory, account_id)

    second = await _rotate(db_session_factory, account_id)

    stored = await _stored(db_session_factory, account_id)
    assert in_flight == [first]
    assert second != first
    assert await _load(db_session_factory, account_id) == [second, first]
    assert stored.previous_expires_at == stored.issued_at + timedelta(
        seconds=build_service_settings().provider_secret_grace_seconds,
    )


async def test_after_the_grace_window_only_the_current_secret_signs(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    await _create(db_session_factory, account_id)
    second = await _rotate(db_session_factory, account_id)
    async with db_session_factory.begin() as session:
        await session.execute(
            update(ProviderSigningSecret)
            .where(ProviderSigningSecret.account_id == account_id)
            .values(previous_expires_at=utc_now() - timedelta(seconds=1)),
        )

    assert await _load(db_session_factory, account_id) == [second]


async def test_rotating_again_replaces_the_previous_secret(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    await _create(db_session_factory, account_id)
    second = await _rotate(db_session_factory, account_id)

    third = await _rotate(db_session_factory, account_id)

    assert await _load(db_session_factory, account_id) == [third, second]


async def test_concurrent_rotations_chain_one_after_the_other(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    first = await _create(db_session_factory, account_id)

    rotated = await asyncio.gather(
        _rotate(db_session_factory, account_id),
        _rotate(db_session_factory, account_id),
    )

    signing = await _load(db_session_factory, account_id)
    assert sorted(signing) == sorted(rotated)
    assert first not in signing


async def test_rotating_without_a_secret_is_not_found(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    with pytest.raises(NotFoundError, match="has no signing secret; create one first"):
        await _rotate(db_session_factory, account_id)


async def test_loading_signing_secrets_without_a_secret_is_an_invalid_state(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    with pytest.raises(InvalidStateError, match="the provider has no signing secret"):
        await _load(db_session_factory, account_id)


async def test_a_changed_encryption_key_fails_closed_until_the_old_key_is_listed_after_it(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    first = await _create(db_session_factory, account_id)
    new_key = Fernet.generate_key().decode()

    with pytest.raises(InvalidStateError, match="cannot be decrypted"):
        await _load(db_session_factory, account_id, settings=_settings_with_keys(new_key))

    both_keys = _settings_with_keys(new_key, TEST_PROVIDER_SECRET_ENCRYPTION_KEY)
    assert await _load(db_session_factory, account_id, settings=both_keys) == [first]

    second = await _rotate(db_session_factory, account_id, settings=both_keys)

    stored = await _stored(db_session_factory, account_id)
    assert Fernet(new_key).decrypt(stored.ciphertext).decode() == second
    assert await _load(db_session_factory, account_id, settings=both_keys) == [second, first]


async def test_a_previous_secret_under_a_removed_key_is_skipped_and_logged(
    db_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The old key was removed (after a compromise, say) and the provider rotated, as the
    # recovery asks: the secret it replaced cannot be decrypted, but the new one signs.
    account_id = await create_provider_account_record(db_session_factory)
    await _create(db_session_factory, account_id)
    new_key_only = _settings_with_keys(Fernet.generate_key().decode())
    new = await _rotate(db_session_factory, account_id, settings=new_key_only)
    stored = await _stored(db_session_factory, account_id)

    with caplog.at_level(logging.WARNING, logger="app.services.provider_signing_secrets"):
        signing = await _load(db_session_factory, account_id, settings=new_key_only)

    assert signing == [new]
    (record,) = [
        record
        for record in caplog.records
        if record.name == "app.services.provider_signing_secrets"
    ]
    assert (record.levelno, record.getMessage()) == (
        logging.WARNING,
        "previous signing secret cannot be decrypted; it no longer signs",
    )
    assert getattr(record, "account_id", None) == account_id
    assert stored.previous_ciphertext is not None
    assert stored.previous_ciphertext not in caplog.text
