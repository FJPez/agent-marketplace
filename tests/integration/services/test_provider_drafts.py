import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import create_provider_account_record, create_service_record

from app.core.enums import ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError, NotFoundError
from app.core.text import SERVICE_TAGS_MAX_COUNT
from app.db.models import Service, ServiceRevision, ServiceTag
from app.services.provider_drafts import (
    create_service,
    get_service,
    list_services,
    replace_tags,
    update_service,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("migrated_database"),
]


async def test_create_service_persists_draft_service_with_stripped_fields(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        service = await create_service(
            session=session,
            account_id=account_id,
            slug="demo-agent-service",
            name="  My Name  ",
            summary="  My Summary  ",
            description="  My Description  ",
        )

    async with db_session_factory() as session:
        persisted = await session.get(Service, service.id)

    assert persisted is not None
    assert persisted.provider_account_id == account_id
    assert persisted.slug == "demo-agent-service"
    assert persisted.name == "My Name"
    assert persisted.summary == "My Summary"
    assert persisted.description == "My Description"
    assert persisted.lifecycle is ServiceLifecycle.DRAFT


async def test_create_service_rejects_duplicate_slug(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    other_account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        await create_service(
            session=session,
            account_id=account_id,
            slug="translation-service",
            name="Translation Service",
            summary="A summary",
            description=None,
        )

    async with db_session_factory() as session:
        with pytest.raises(ConflictError):
            await create_service(
                session=session,
                account_id=other_account_id,
                slug="translation-service",
                name="Another Name",
                summary="Another summary",
                description=None,
            )


@pytest.mark.parametrize(
    ("slug", "name", "summary", "description"),
    [
        ("Bad_Slug", "Name", "Summary", None),
        ("123", "Name", "Summary", None),
        ("valid-slug", "", "Summary", None),
        ("valid-slug", "x" * 256, "Summary", None),
        ("valid-slug", "Name", "", None),
    ],
)
async def test_create_service_rejects_invalid_input(
    db_session_factory: async_sessionmaker[AsyncSession],
    slug: str,
    name: str,
    summary: str,
    description: str | None,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await create_service(
                session=session,
                account_id=account_id,
                slug=slug,
                name=name,
                summary=summary,
                description=description,
            )


async def test_list_services_returns_only_own_services_ordered_desc(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    other_account_id = await create_provider_account_record(db_session_factory)
    await create_service_record(
        db_session_factory,
        provider_account_id=other_account_id,
        slug="other-service",
    )

    created_ids: list[int] = []
    for index in range(3):
        service_id = await create_service_record(
            db_session_factory,
            provider_account_id=account_id,
            slug=f"service-{index}",
        )
        created_ids.append(service_id)

    async with db_session_factory() as session:
        services = await list_services(session=session, account_id=account_id)

    assert [service.id for service in services] == sorted(created_ids, reverse=True)
    assert all(service.provider_account_id == account_id for service in services)


async def test_get_service_raises_not_found_for_missing_id(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await get_service(session=session, account_id=account_id, service_id=999_999)


async def test_get_service_raises_not_found_for_other_accounts_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    other_account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=other_account_id,
        slug="other-service",
    )

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await get_service(session=session, account_id=account_id, service_id=service_id)


async def test_update_service_rejects_empty_updates(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await update_service(
                session=session,
                account_id=account_id,
                service_id=service_id,
                updates={},
            )


async def test_update_service_rejects_unknown_field(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await update_service(
                session=session,
                account_id=account_id,
                service_id=service_id,
                updates={"slug": "new-slug"},
            )


@pytest.mark.parametrize("field", ["name", "summary"])
async def test_update_service_rejects_null_required_field(
    db_session_factory: async_sessionmaker[AsyncSession],
    field: str,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await update_service(
                session=session,
                account_id=account_id,
                service_id=service_id,
                updates={field: None},
            )


async def test_update_service_clears_description_when_set_to_null(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        description="original description",
        lifecycle=ServiceLifecycle.DRAFT,
    )

    async with db_session_factory() as session:
        await update_service(
            session=session,
            account_id=account_id,
            service_id=service_id,
            updates={"description": None},
        )

    async with db_session_factory() as session:
        persisted = await session.get(Service, service_id)

    assert persisted is not None
    assert persisted.description is None


async def test_update_service_draft_persists_name_and_summary_and_bumps_updated_at(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )

    async with db_session_factory() as session:
        before = await session.get(Service, service_id)
        assert before is not None
        before_updated_at = before.updated_at

    async with db_session_factory() as session:
        await update_service(
            session=session,
            account_id=account_id,
            service_id=service_id,
            updates={"name": "  New Name  ", "summary": "  New Summary  "},
        )

    async with db_session_factory() as session:
        persisted = await session.get(Service, service_id)

    assert persisted is not None
    assert persisted.name == "New Name"
    assert persisted.summary == "New Summary"
    assert persisted.updated_at > before_updated_at


async def test_update_service_active_non_material_update_succeeds_without_revision(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )

    async with db_session_factory() as session:
        await update_service(
            session=session,
            account_id=account_id,
            service_id=service_id,
            updates={
                "name": "Active Name",
                "summary": "Active Summary",
                "description": "Active Description",
            },
        )

    async with db_session_factory() as session:
        persisted = await session.get(Service, service_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(
                ServiceRevision.service_id == service_id,
            ),
        )

    assert persisted is not None
    assert persisted.name == "Active Name"
    assert revision_count == 0


async def test_update_service_suspended_lifecycle_raises_invalid_state(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.SUSPENDED,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError):
            await update_service(
                session=session,
                account_id=account_id,
                service_id=service_id,
                updates={"name": "New Name"},
            )


async def test_replace_tags_persists_normalized_sorted_tags(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )

    async with db_session_factory() as session:
        await replace_tags(
            session=session,
            account_id=account_id,
            service_id=service_id,
            tags=["  Demo ", "translation", "demo"],
        )

    async with db_session_factory() as session:
        tags = list(
            await session.scalars(
                select(ServiceTag.tag)
                .where(ServiceTag.service_id == service_id)
                .order_by(ServiceTag.tag),
            ),
        )

    assert tags == ["demo", "translation"]


async def test_replace_tags_fully_replaces_existing_rows(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
        tags=["demo", "translation"],
    )

    async with db_session_factory() as session:
        await replace_tags(
            session=session,
            account_id=account_id,
            service_id=service_id,
            tags=["billing"],
        )

    async with db_session_factory() as session:
        tags = list(
            await session.scalars(
                select(ServiceTag.tag).where(ServiceTag.service_id == service_id),
            ),
        )

    assert tags == ["billing"]


async def test_create_service_raises_integrity_error_for_unknown_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        with pytest.raises(IntegrityError):
            await create_service(
                session=session,
                account_id=999_999,
                slug="orphaned-service",
                name="Orphaned",
                summary="A summary",
                description=None,
            )


@pytest.mark.parametrize("tag", ["x" * 65, "bad tag!"])
async def test_replace_tags_rejects_invalid_tag_values(
    db_session_factory: async_sessionmaker[AsyncSession],
    tag: str,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await replace_tags(
                session=session,
                account_id=account_id,
                service_id=service_id,
                tags=[tag],
            )


async def test_replace_tags_rejects_more_than_max_tags(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await replace_tags(
                session=session,
                account_id=account_id,
                service_id=service_id,
                tags=[f"tag-{index}" for index in range(SERVICE_TAGS_MAX_COUNT + 1)],
            )


async def test_replace_tags_rejects_active_lifecycle(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError):
            await replace_tags(
                session=session,
                account_id=account_id,
                service_id=service_id,
                tags=["demo"],
            )
