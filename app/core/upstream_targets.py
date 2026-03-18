from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address
from socket import SOCK_STREAM, getaddrinfo
from urllib.parse import urlsplit

from app.core.config import Settings, get_settings
from app.core.enums import AppEnv

ResolvedIpAddress = IPv4Address | IPv6Address
_ALLOWED_LOOPBACK_ENVS = {AppEnv.DEV, AppEnv.TEST}
_METADATA_HOSTS = {
    "metadata",
    "metadata.google.internal",
    "metadata.google.internal.",
    "metadata.aws.internal",
    "instance-data",
    "instance-data.ec2.internal",
}


class UnsafeUpstreamTargetError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("upstream target is not allowed")


def validate_upstream_base_url(
    base_url: str,
    *,
    settings: Settings | None = None,
) -> str:
    resolved_settings = settings or get_settings()
    parsed = urlsplit(base_url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if host is None or scheme not in {"http", "https"}:
        raise UnsafeUpstreamTargetError("upstream target must use http or https with a host")

    normalized_host = host.rstrip(".").lower()
    if normalized_host in _METADATA_HOSTS:
        raise UnsafeUpstreamTargetError("metadata targets are not allowed")

    port = parsed.port or (443 if scheme == "https" else 80)
    resolved_ips = _resolve_host_ips(normalized_host, port)
    if not resolved_ips:
        raise UnsafeUpstreamTargetError("upstream target could not be resolved")

    if scheme == "http":
        if resolved_settings.env not in _ALLOWED_LOOPBACK_ENVS:
            raise UnsafeUpstreamTargetError("plain HTTP upstreams are only allowed in dev/test")
        if not all(ip.is_loopback for ip in resolved_ips):
            raise UnsafeUpstreamTargetError("plain HTTP upstreams must target loopback addresses")
        return base_url

    if any(not ip.is_global for ip in resolved_ips):
        raise UnsafeUpstreamTargetError("HTTPS upstreams must resolve to public addresses")

    return base_url


def _resolve_host_ips(host: str, port: int) -> set[ResolvedIpAddress]:
    try:
        return {ip_address(host)}
    except ValueError:
        pass

    try:
        resolved = getaddrinfo(host, port, type=SOCK_STREAM)
    except OSError as exc:
        raise UnsafeUpstreamTargetError("upstream target could not be resolved") from exc

    ips: set[ResolvedIpAddress] = set()
    for _, _, _, _, sockaddr in resolved:
        candidate = sockaddr[0]
        if not isinstance(candidate, str):
            continue
        if "%" in candidate:
            candidate = candidate.split("%", 1)[0]
        ips.add(ip_address(candidate))
    return ips
