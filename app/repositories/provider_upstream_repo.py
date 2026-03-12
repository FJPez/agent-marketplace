from datetime import UTC, datetime

from app.db.models.provider_upstream import ProviderUpstream
from app.db.models.service_endpoint import ServiceEndpoint


class ProviderUpstreamRepository:
    def upsert(
        self,
        endpoint: ServiceEndpoint,
        *,
        base_url: str,
        path: str,
        http_method: str,
        config: dict[str, object],
    ) -> ProviderUpstream:
        upstream = endpoint.__dict__.get("upstream")
        if upstream is None:
            upstream = ProviderUpstream(
                endpoint_id=endpoint.id,
                base_url=base_url,
                path=path,
                http_method=http_method,
                config=config,
            )
            endpoint.upstream = upstream
            return upstream

        upstream.base_url = base_url
        upstream.path = path
        upstream.http_method = http_method
        upstream.config = config
        upstream.updated_at = datetime.now(UTC)
        return upstream
