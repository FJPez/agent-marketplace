import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from tests.helpers.dns import TEST_UPSTREAM_ADDRESS, TEST_UPSTREAM_HOST, FakeResolver

from app.core.enums import ServiceHealthStatus
from app.integrations.providers.dns import IpAddress
from app.services.domain_control import (
    MAX_CONCURRENT_HOST_PROOFS,
    check_domain_control,
    record_value,
)
from app.services.service_health import ServiceHealthOutcome

TOKEN = "provider-token"
RECORD_NAME = f"_agent-marketplace.{TEST_UPSTREAM_HOST}"
OTHER_HOST = "api.other-provider.example"
OTHER_RECORD_NAME = f"_agent-marketplace.{OTHER_HOST}"
FAILED = "upstream hosts failed the domain-control check"
RECORD_MISSING_REMEDY = (
    "record_missing: publish a TXT record at _agent-marketplace.<host> with the value from "
    "POST /v1/provider/domain-verification. DNS changes can take minutes to be visible, "
    "longer after a failed check because resolvers cache the miss (negative caching)."
)
NOT_PUBLIC_REMEDY = (
    "not_public: make the host resolve, and only to public addresses; if its DNS lookup "
    "failed, retry."
)
LOOKUP_FAILED_REMEDY = "lookup_failed: the DNS lookup failed; retry."


def _resolver(*txt_values: str) -> FakeResolver:
    return FakeResolver(
        {TEST_UPSTREAM_HOST: [TEST_UPSTREAM_ADDRESS]},
        txt_records={RECORD_NAME: list(txt_values)},
    )


async def test_a_host_carrying_the_providers_record_passes() -> None:
    # Other TXT records at the name (an SPF record here) and whitespace do not matter.
    resolver = _resolver("v=spf1 -all", f"  {record_value(TOKEN)} ")

    outcome = await check_domain_control(
        resolver=resolver,
        token=TOKEN,
        hosts={TEST_UPSTREAM_HOST},
    )

    assert outcome == ServiceHealthOutcome(
        status=ServiceHealthStatus.PASS,
        summary="the provider controls every upstream host",
        details={"hosts": {TEST_UPSTREAM_HOST: "verified"}},
    )


@pytest.mark.parametrize(
    ("resolver", "proof"),
    [
        pytest.param(_resolver(), "record_missing", id="no_record"),
        pytest.param(
            _resolver(record_value("another-providers-token")),
            "record_missing",
            id="another_accounts_token",
        ),
        pytest.param(
            _resolver(f"agent-marketplace-verification={TOKEN.upper()}"),
            "record_missing",
            id="token_in_another_case",
        ),
        pytest.param(
            _resolver(f"{record_value(TOKEN)} and more"),
            "record_missing",
            id="extra_text_after_the_value",
        ),
        pytest.param(_resolver(record_value(TOKEN[:-1])), "record_missing", id="token_prefix"),
        pytest.param(
            _resolver(f"v=spf1 {record_value(TOKEN)} -all"),
            "record_missing",
            id="value_inside_a_longer_string",
        ),
        pytest.param(
            FakeResolver(
                {TEST_UPSTREAM_HOST: [TEST_UPSTREAM_ADDRESS]},
                failing_names={RECORD_NAME},
            ),
            "lookup_failed",
            id="txt_lookup_failed",
        ),
        pytest.param(
            FakeResolver(
                {TEST_UPSTREAM_HOST: [TEST_UPSTREAM_ADDRESS, "10.0.0.1"]},
                txt_records={RECORD_NAME: [record_value(TOKEN)]},
            ),
            "not_public",
            id="private_address",
        ),
        pytest.param(
            FakeResolver(txt_records={RECORD_NAME: [record_value(TOKEN)]}),
            "not_public",
            id="no_address",
        ),
    ],
)
async def test_a_host_without_a_public_address_and_the_providers_record_fails(
    resolver: FakeResolver,
    proof: str,
) -> None:
    outcome = await check_domain_control(
        resolver=resolver,
        token=TOKEN,
        hosts={TEST_UPSTREAM_HOST},
    )

    assert outcome.status is ServiceHealthStatus.FAIL
    assert outcome.summary.startswith(f"{FAILED}: {TEST_UPSTREAM_HOST} ({proof}). {proof}: ")
    assert outcome.details == {"hosts": {TEST_UPSTREAM_HOST: proof}}


@pytest.mark.parametrize(
    ("resolver", "remedy"),
    [
        pytest.param(_resolver(), RECORD_MISSING_REMEDY, id="record_missing"),
        pytest.param(
            FakeResolver({TEST_UPSTREAM_HOST: ["10.0.0.1"]}),
            NOT_PUBLIC_REMEDY,
            id="not_public",
        ),
        pytest.param(
            FakeResolver(failing_names={TEST_UPSTREAM_HOST}),
            NOT_PUBLIC_REMEDY,
            id="address_lookup_failed",
        ),
        pytest.param(
            FakeResolver(
                {TEST_UPSTREAM_HOST: [TEST_UPSTREAM_ADDRESS]},
                failing_names={RECORD_NAME},
            ),
            LOOKUP_FAILED_REMEDY,
            id="lookup_failed",
        ),
    ],
)
async def test_each_kind_of_failure_gets_its_own_remedy(
    resolver: FakeResolver,
    remedy: str,
) -> None:
    outcome = await check_domain_control(
        resolver=resolver,
        token=TOKEN,
        hosts={TEST_UPSTREAM_HOST},
    )

    proof = remedy.partition(":")[0]
    assert outcome.summary == f"{FAILED}: {TEST_UPSTREAM_HOST} ({proof}). {remedy}"


async def test_every_host_is_checked_and_only_the_failing_ones_are_named() -> None:
    resolver = _resolver(record_value(TOKEN))
    resolver.addresses[OTHER_HOST] = [TEST_UPSTREAM_ADDRESS]

    outcome = await check_domain_control(
        resolver=resolver,
        token=TOKEN,
        hosts={TEST_UPSTREAM_HOST, OTHER_HOST},
    )

    assert outcome == ServiceHealthOutcome(
        status=ServiceHealthStatus.FAIL,
        summary=f"{FAILED}: {OTHER_HOST} (record_missing). {RECORD_MISSING_REMEDY}",
        details={"hosts": {OTHER_HOST: "record_missing", TEST_UPSTREAM_HOST: "verified"}},
    )


async def test_hosts_failing_differently_get_every_remedy_that_applies() -> None:
    resolver = FakeResolver(
        {TEST_UPSTREAM_HOST: ["10.0.0.1"], OTHER_HOST: [TEST_UPSTREAM_ADDRESS]},
        failing_names={OTHER_RECORD_NAME},
    )

    outcome = await check_domain_control(
        resolver=resolver,
        token=TOKEN,
        hosts={TEST_UPSTREAM_HOST, OTHER_HOST},
    )

    assert outcome.summary == (
        f"{FAILED}: {OTHER_HOST} (lookup_failed), {TEST_UPSTREAM_HOST} (not_public). "
        f"{NOT_PUBLIC_REMEDY} {LOOKUP_FAILED_REMEDY}"
    )


async def test_a_failed_record_lookup_is_logged_for_operators(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resolver = FakeResolver(
        {TEST_UPSTREAM_HOST: [TEST_UPSTREAM_ADDRESS]},
        failing_names={RECORD_NAME},
    )

    with caplog.at_level(logging.WARNING, logger="app.services.domain_control"):
        await check_domain_control(resolver=resolver, token=TOKEN, hosts={TEST_UPSTREAM_HOST})

    (record,) = [
        record for record in caplog.records if record.name == "app.services.domain_control"
    ]
    assert (record.levelno, record.getMessage()) == (
        logging.WARNING,
        "domain verification record lookup failed",
    )
    assert getattr(record, "record_name", None) == RECORD_NAME
    assert f"DnsLookupError: DNS lookup for {RECORD_NAME} failed" in caplog.text


class _ConcurrencyRecordingResolver(FakeResolver):
    """Records the most lookups in flight at once; every lookup yields to the loop first."""

    def __init__(self, hosts: list[str]) -> None:
        super().__init__(
            {host: [TEST_UPSTREAM_ADDRESS] for host in hosts},
            txt_records={f"_agent-marketplace.{host}": [record_value(TOKEN)] for host in hosts},
        )
        self.in_flight = 0
        self.most_in_flight = 0

    async def resolve_addresses(self, host: str) -> list[IpAddress]:
        async with self._in_flight():
            return await super().resolve_addresses(host)

    async def resolve_txt(self, name: str) -> list[str]:
        async with self._in_flight():
            return await super().resolve_txt(name)

    @asynccontextmanager
    async def _in_flight(self) -> AsyncIterator[None]:
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            # Every other proof that may start does so before this lookup ends.
            await asyncio.sleep(0)
            yield
        finally:
            self.in_flight -= 1


async def test_at_most_a_few_hosts_are_proven_at_once() -> None:
    hosts = [f"api{number}.provider.example" for number in range(3 * MAX_CONCURRENT_HOST_PROOFS)]
    resolver = _ConcurrencyRecordingResolver(hosts)

    outcome = await check_domain_control(resolver=resolver, token=TOKEN, hosts=set(hosts))

    assert outcome.status is ServiceHealthStatus.PASS
    # Bounded, but still concurrent.
    assert resolver.most_in_flight == MAX_CONCURRENT_HOST_PROOFS


async def test_a_provider_without_a_token_fails_without_a_lookup() -> None:
    resolver = FakeResolver(failing_names={TEST_UPSTREAM_HOST, RECORD_NAME})

    outcome = await check_domain_control(
        resolver=resolver,
        token=None,
        hosts={TEST_UPSTREAM_HOST},
    )

    assert outcome == ServiceHealthOutcome(
        status=ServiceHealthStatus.FAIL,
        summary=(
            "the provider has no domain verification token; request one with "
            "POST /v1/provider/domain-verification"
        ),
    )


async def test_no_hosts_to_prove_fails_closed() -> None:
    outcome = await check_domain_control(resolver=FakeResolver(), token=TOKEN, hosts=set())

    assert outcome == ServiceHealthOutcome(
        status=ServiceHealthStatus.FAIL,
        summary="the service has no upstream hosts to prove control of",
    )
