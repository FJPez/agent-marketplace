from ipaddress import ip_address

import pytest
from tests.helpers.dns import FakeResolver

from app.integrations.providers.targets import (
    UnsafeUpstreamTargetError,
    UpstreamTarget,
    resolve_upstream_target,
)

HOST = "api.provider.example"
PUBLIC_IPV4 = "93.184.215.14"
PUBLIC_IPV6 = "2606:4700:4700::1111"


async def test_a_valid_target_carries_its_host_and_every_address_to_pin() -> None:
    resolver = FakeResolver({HOST: [PUBLIC_IPV4, PUBLIC_IPV6]})

    target = await resolve_upstream_target(f"https://{HOST}/v1/", resolver=resolver)

    assert target == UpstreamTarget(
        base_url=f"https://{HOST}/v1/",
        host=HOST,
        addresses=(ip_address(PUBLIC_IPV4), ip_address(PUBLIC_IPV6)),
    )


@pytest.mark.parametrize(
    ("base_url", "reason"),
    [
        (f"http://{HOST}/", "must use https"),
        (f"https://user:password@{HOST}/", "must not carry credentials"),
        (f"https://@{HOST}/", "must not carry credentials"),
        (f"https://{HOST}/?key=value", "must not carry a query string or fragment"),
        (f"https://{HOST}/?", "must not carry a query string or fragment"),
        (f"https://{HOST}/#section", "must not carry a query string or fragment"),
        (f"https://{HOST}:8443/", "must use the https port 443"),
        (f"https://{HOST}:99999/", "must use the https port 443"),
        ("https://93.184.215.14/", "must be a DNS name"),
        ("https://[2606:4700:4700::1111]/", "must be a DNS name"),
        ("https://1572394766/", "must be a DNS name"),
        ("https://0x5d.0xb8.0xd7.0x0e/", "must be a DNS name"),
        ("https://localhost/", "must be a DNS name"),
        (f"https://{HOST}./", "must be a DNS name"),
        ("https://under_score.provider.example/", "must be a DNS name"),
    ],
    ids=[
        "http",
        "userinfo",
        "empty_userinfo",
        "query",
        "empty_query",
        "fragment",
        "other_port",
        "invalid_port",
        "ipv4_literal",
        "ipv6_literal",
        "decimal_ipv4",
        "hex_ipv4",
        "single_label",
        "trailing_dot",
        "underscore",
    ],
)
async def test_an_unsafe_url_is_rejected_before_any_lookup(base_url: str, reason: str) -> None:
    # Every host the URLs name resolves publicly, so only the URL check can reject them.
    resolver = FakeResolver(
        {
            HOST: [PUBLIC_IPV4],
            "localhost": [PUBLIC_IPV4],
            f"{HOST}.": [PUBLIC_IPV4],
            "under_score.provider.example": [PUBLIC_IPV4],
        },
    )

    with pytest.raises(UnsafeUpstreamTargetError, match=reason):
        await resolve_upstream_target(base_url, resolver=resolver)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "100.100.100.200",
        "0.0.0.0",
        "255.255.255.255",
        "192.0.2.1",
        # Special-purpose only since the table CPython 3.12.4 corrected (CVE-2024-4032).
        "192.0.0.192",
        "224.0.0.1",
        "239.255.255.250",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd00:ec2::254",
        "2001:db8::1",
        "2001::1",
        "2002:7f00:1::1",
        "ff02::1",
        "ff0e::1",
        "::ffff:127.0.0.1",
        "::ffff:224.0.0.1",
        "::127.0.0.1",
        "64:ff9b::7f00:1",
        "64:ff9b::a9fe:a9fe",
        # Deprecated site-local.
        "fec0::1",
        "fed0::1",
        "feff::1",
        # IPv4-translated (SIIT) forms of private addresses.
        "::ffff:0:a00:1",
        "::ffff:0:7f00:1",
        "::ffff:0:a9fe:a9fe",
        # Beside the well-known NAT64 prefix, outside it.
        "64:ff9b::ffff:a00:1",
        "64:ff9b::1:a00:1",
        "64:ff9b:0:1::a00:1",
        "64:ff9b:2::a00:1",
        "64:ff9b:a00:1::",
        # The rest of ::/8.
        "::1:a00:1",
        "::fffe:a00:1",
        "::ffff:1:a00:1",
        "0:0:1::a00:1",
        "::1:0:0:1",
        # Reserved beside the discard-only 100::/64, and segment routing SIDs.
        "100:0:0:1::1",
        "5f00::1",
        # Unallocated.
        "180::1",
        "200::1",
        "400::1",
        "4000::1",
        "8000::1",
        "e000::1",
        "f000::1",
        "fe00::1",
        # ISATAP interface ids carrying a private IPv4 address.
        "2606:4700::5efe:a00:1",
        "2606:4700::200:5efe:a00:1",
        "2001:4860::5efe:a9fe:a9fe",
        # Special-purpose blocks inside 2001::/23.
        "2001:20::1",
        "2001:3::1",
        "2001:4:112::1",
        "2001:30::1",
    ],
)
async def test_a_host_with_any_non_public_address_is_rejected(address: str) -> None:
    resolver = FakeResolver({HOST: [PUBLIC_IPV4, address]})

    with pytest.raises(UnsafeUpstreamTargetError, match="must resolve, and only to public"):
        await resolve_upstream_target(f"https://{HOST}/", resolver=resolver)


@pytest.mark.parametrize(
    "address",
    ["::ffff:93.184.215.14", "64:ff9b::5db8:d70e", "2606:4700::5efe:5db8:d70e"],
    ids=["ipv4_mapped", "nat64", "isatap"],
)
async def test_ipv6_forms_of_a_public_ipv4_address_are_accepted(address: str) -> None:
    resolver = FakeResolver({HOST: [address]})

    target = await resolve_upstream_target(f"https://{HOST}/", resolver=resolver)

    assert target.addresses == (ip_address(address),)


@pytest.mark.parametrize(
    "resolver",
    [FakeResolver(), FakeResolver(failing_names={HOST})],
    ids=["no_records", "lookup_failed"],
)
async def test_a_host_that_does_not_resolve_is_rejected(resolver: FakeResolver) -> None:
    with pytest.raises(UnsafeUpstreamTargetError, match="must resolve, and only to public"):
        await resolve_upstream_target(f"https://{HOST}/", resolver=resolver)
