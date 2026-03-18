from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_object_session

from app.core.json_types import JsonObject
from app.db.models.provider_upstream import ProviderUpstream
from app.db.models.service_endpoint import ServiceEndpoint


class ProviderUpstreamRepository:
    async def upsert(
        self,
        endpoint: ServiceEndpoint,
        *,
        base_url: str,
        path: str,
        http_method: str,
        config: JsonObject,
    ) -> ProviderUpstream:
        if endpoint.id is None:
            raise ValueError("endpoint must be persisted before upstream upsert")

        session = async_object_session(endpoint)
        if session is None:
            raise ValueError("endpoint must be attached to an async session")

        upstream = await session.get(ProviderUpstream, endpoint.id)
        if upstream is None:
            upstream = ProviderUpstream(
                endpoint_id=endpoint.id,
                base_url=base_url,
                path=path,
                http_method=http_method,
                config=config,
            )
            session.add(upstream)
            endpoint.upstream = upstream
            return upstream

        upstream.base_url = base_url
        upstream.path = path
        upstream.http_method = http_method
        upstream.config = config
        upstream.updated_at = datetime.now(UTC)
        return upstream
