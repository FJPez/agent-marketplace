from typing import Annotated

from fastapi import Depends, Request

from app.core.lifespan import get_resources
from app.integrations.providers.dns import DnsResolver


def get_dns_resolver(request: Request) -> DnsResolver:
    return get_resources(request.app).dns_resolver


DnsResolverDep = Annotated[DnsResolver, Depends(get_dns_resolver)]
