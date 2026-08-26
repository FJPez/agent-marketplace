"""Shared owned-access loaders for the provider service graph."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.errors import NotFoundError
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint


async def load_owned_service(
    *,
    session: AsyncSession,
    account_id: int,
    service_id: int,
) -> Service:
    statement = (
        select(Service)
        .options(
            selectinload(Service.tags),
            selectinload(Service.endpoints).selectinload(ServiceEndpoint.price),
            selectinload(Service.endpoints).selectinload(ServiceEndpoint.upstream),
        )
        # Rows already in the identity map may predate a lock wait; refresh
        # them so callers see the state the lock now protects.
        .execution_options(populate_existing=True)
        .where(
            Service.id == service_id,
            Service.provider_account_id == account_id,
        )
    )
    service = await session.scalar(statement)
    if service is None:
        raise NotFoundError("service not found")
    return service


async def lock_owned_service(
    *,
    session: AsyncSession,
    account_id: int,
    service_id: int,
) -> Service:
    statement = (
        select(Service)
        .execution_options(populate_existing=True)
        .where(
            Service.id == service_id,
            Service.provider_account_id == account_id,
        )
        .with_for_update()
    )
    service = await session.scalar(statement)
    if service is None:
        raise NotFoundError("service not found")
    return service


async def load_owned_service_for_update(
    *,
    session: AsyncSession,
    account_id: int,
    service_id: int,
) -> Service:
    # Lock before loading the graph so the eager-loaded rows reflect the
    # state after any concurrent mutation has committed.
    await lock_owned_service(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )
    return await load_owned_service(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )


async def lock_owned_service_by_endpoint(
    *,
    session: AsyncSession,
    account_id: int,
    endpoint_id: int,
) -> int:
    locked_service_id = await session.scalar(
        select(Service.id)
        .join(Service.endpoints)
        .where(
            ServiceEndpoint.id == endpoint_id,
            Service.provider_account_id == account_id,
        )
        .with_for_update(),
    )
    if locked_service_id is None:
        raise NotFoundError("endpoint not found")
    return locked_service_id


async def load_owned_endpoint(
    *,
    session: AsyncSession,
    account_id: int,
    endpoint_id: int,
) -> ServiceEndpoint:
    statement = (
        select(ServiceEndpoint)
        .join(Service)
        .options(
            joinedload(ServiceEndpoint.service),
            selectinload(ServiceEndpoint.price),
            selectinload(ServiceEndpoint.upstream),
        )
        .execution_options(populate_existing=True)
        .where(
            ServiceEndpoint.id == endpoint_id,
            Service.provider_account_id == account_id,
        )
    )
    endpoint = await session.scalar(statement)
    if endpoint is None:
        raise NotFoundError("endpoint not found")
    return endpoint
