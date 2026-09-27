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
    ],
)
async def test_a_host_with_any_non_public_address_is_rejected(address: str) -> None:
    resolver = FakeResolver({HOST: [PUBLIC_IPV4, address]})

    with pytest.raises(UnsafeUpstreamTargetError, match="must resolve, and only to public"):
        await resolve_upstream_target(f"https://{HOST}/", resolver=resolver)


@pytest.mark.parametrize(
    "address",
    ["::ffff:93.184.215.14", "64:ff9b::5db8:d70e"],
    ids=["ipv4_mapped", "nat64"],
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
