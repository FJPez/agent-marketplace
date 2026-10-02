import logging
from ipaddress import ip_address

import pytest
from pydantic import HttpUrl
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
        pytest.param(f"http://{HOST}/", "must use https", id="http"),
        pytest.param(f"https://user:password@{HOST}/", "must not carry credentials", id="userinfo"),
        pytest.param(f"https://@{HOST}/", "must not carry credentials", id="empty_userinfo"),
        pytest.param(
            f"https://{HOST}/?key=value", "must not carry a query string or fragment", id="query"
        ),
        pytest.param(
            f"https://{HOST}/?", "must not carry a query string or fragment", id="empty_query"
        ),
        pytest.param(
            f"https://{HOST}/#section", "must not carry a query string or fragment", id="fragment"
        ),
        pytest.param(f"https://{HOST}:8443/", "must use the https port 443", id="other_port"),
        pytest.param(f"https://{HOST}:99999/", "must use the https port 443", id="invalid_port"),
        pytest.param("https://93.184.215.14/", "must be a DNS name", id="ipv4_literal"),
        pytest.param("https://[2606:4700:4700::1111]/", "must be a DNS name", id="ipv6_literal"),
        pytest.param("https://1572394766/", "must be a DNS name", id="decimal_ipv4"),
        pytest.param("https://0x5d.0xb8.0xd7.0x0e/", "must be a DNS name", id="hex_ipv4"),
        pytest.param("https://localhost/", "must be a DNS name", id="single_label"),
        pytest.param(f"https://{HOST}./", "must be a DNS name", id="trailing_dot"),
        pytest.param(
            "https://under_score.provider.example/", "must be a DNS name", id="underscore"
        ),
        pytest.param("https://[v1.x]/", "must be a DNS name", id="bracketed_ipvfuture"),
        pytest.param("https://[::1/", "must be a DNS name", id="unclosed_bracket"),
        pytest.param(f"https://{HOST}]/", "must be a DNS name", id="stray_bracket"),
        pytest.param(f" https://{HOST}/", "must be printable ASCII", id="leading_space"),
        pytest.param(f"https://{HOST}/a b", "must be printable ASCII", id="space"),
        pytest.param("https://api.provider\n.example/", "must be printable ASCII", id="newline"),
        pytest.param(f"https://{HOST}/\t", "must be printable ASCII", id="tab"),
        pytest.param(f"https://{HOST}/\x7f", "must be printable ASCII", id="delete"),
        pytest.param(
            f"https://{HOST}\uff03@evil.example/",
            "must be printable ASCII",
            id="fullwidth_number_sign",
        ),
        pytest.param(
            f"https://{HOST}\uff0fevil/", "must be printable ASCII", id="fullwidth_solidus"
        ),
        pytest.param("https://\u212aube.example.com/", "must be printable ASCII", id="kelvin_sign"),
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
            "v1.x": [PUBLIC_IPV4],
            "kube.example.com": [PUBLIC_IPV4],
        },
    )

    with pytest.raises(UnsafeUpstreamTargetError, match=reason):
        await resolve_upstream_target(base_url, resolver=resolver)


@pytest.mark.parametrize(
    "base_url",
    [
        f"HTTPS://{HOST}/v1",
        f"https://{HOST.upper()}/v1",
        f"https://{HOST}:443/v1",
        f"https://{HOST}:0443/v1",
        f"https://{HOST}:/v1",
    ],
    ids=["uppercase_scheme", "uppercase_host", "default_port", "zero_padded_port", "empty_port"],
)
async def test_an_accepted_url_is_returned_rebuilt_from_its_validated_parts(base_url: str) -> None:
    resolver = FakeResolver({HOST: [PUBLIC_IPV4]})

    target = await resolve_upstream_target(base_url, resolver=resolver)

    assert (target.base_url, target.host) == (f"https://{HOST}/v1", HOST)


@pytest.mark.parametrize(
    "url",
    [
        f"https://{HOST}",
        f"https://{HOST.upper()}:443/v1/",
        f"https://{HOST}/a b/%7Ec/[x]",
        "https://b\u00fccher.example/\u00e9",
    ],
    ids=["bare_host", "default_port", "path_to_encode", "unicode"],
)
async def test_a_url_normalised_by_http_url_is_returned_unchanged(url: str) -> None:
    # The upsert compares the stored URL with the returned one to detect a no-op.
    base_url = str(HttpUrl(url))
    resolver = FakeResolver({HOST: [PUBLIC_IPV4], "xn--bcher-kva.example": [PUBLIC_IPV4]})

    target = await resolve_upstream_target(base_url, resolver=resolver)

    assert target.base_url == base_url


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
        # The returned 6bone test network, and the deprecated 6to4 relay anycast block
        # (RFC 7526) directly and in IPv6 forms; Python counts both as global.
        "3ffe::1",
        "3ffe:ffff::1",
        "192.88.99.1",
        "::ffff:192.88.99.1",
        "64:ff9b::c058:6301",
    ],
)
async def test_a_host_with_any_non_public_address_is_rejected(address: str) -> None:
    resolver = FakeResolver({HOST: [PUBLIC_IPV4, address]})

    with pytest.raises(UnsafeUpstreamTargetError, match="must resolve, and only to public"):
        await resolve_upstream_target(f"https://{HOST}/", resolver=resolver)


@pytest.mark.parametrize(
    ("address", "pinned"),
    [
        (f"::ffff:{PUBLIC_IPV4}", PUBLIC_IPV4),
        ("64:ff9b::5db8:d70e", "64:ff9b::5db8:d70e"),
        ("2606:4700::5efe:5db8:d70e", "2606:4700::5efe:5db8:d70e"),
    ],
    ids=["ipv4_mapped", "nat64", "isatap"],
)
async def test_ipv6_forms_of_a_public_ipv4_address_are_accepted(address: str, pinned: str) -> None:
    resolver = FakeResolver({HOST: [address]})

    target = await resolve_upstream_target(f"https://{HOST}/", resolver=resolver)

    assert target.addresses == (ip_address(pinned),)


async def test_the_addresses_to_pin_are_unmapped_and_deduplicated_in_resolver_order() -> None:
    resolver = FakeResolver(
        {HOST: [PUBLIC_IPV6, f"::ffff:{PUBLIC_IPV4}", PUBLIC_IPV4, PUBLIC_IPV6]}
    )

    target = await resolve_upstream_target(f"https://{HOST}/", resolver=resolver)

    assert target.addresses == (ip_address(PUBLIC_IPV6), ip_address(PUBLIC_IPV4))


@pytest.mark.parametrize(
    "resolver",
    [FakeResolver(), FakeResolver(failing_names={HOST})],
    ids=["no_records", "lookup_failed"],
)
async def test_a_host_that_does_not_resolve_is_rejected(resolver: FakeResolver) -> None:
    with pytest.raises(UnsafeUpstreamTargetError, match="must resolve, and only to public"):
        await resolve_upstream_target(f"https://{HOST}/", resolver=resolver)


async def test_a_failed_lookup_is_logged_for_operators_but_not_told_to_the_provider(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resolver = FakeResolver(failing_names={HOST})

    with (
        caplog.at_level(logging.WARNING, logger="app.integrations.providers"),
        pytest.raises(UnsafeUpstreamTargetError) as rejected,
    ):
        await resolve_upstream_target(f"https://{HOST}/", resolver=resolver)

    assert str(rejected.value) == f"upstream host {HOST} must resolve, and only to public addresses"
    (record,) = [
        record for record in caplog.records if record.name.startswith("app.integrations.providers")
    ]
    assert (record.levelno, record.getMessage()) == (logging.WARNING, "upstream host lookup failed")
    assert getattr(record, "host", None) == HOST
    assert f"DnsLookupError: DNS lookup for {HOST} failed" in caplog.text
