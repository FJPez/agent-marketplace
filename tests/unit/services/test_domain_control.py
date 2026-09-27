import pytest
from tests.helpers.dns import TEST_UPSTREAM_ADDRESS, TEST_UPSTREAM_HOST, FakeResolver

from app.core.enums import ServiceHealthStatus
from app.services.domain_control import check_domain_control, record_value
from app.services.service_health import ServiceHealthOutcome

TOKEN = "provider-token"
RECORD_NAME = f"_agent-marketplace.{TEST_UPSTREAM_HOST}"
OTHER_HOST = "api.other-provider.example"


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
        (_resolver(), "record_missing"),
        (_resolver(record_value("another-providers-token")), "record_missing"),
        (_resolver(f"agent-marketplace-verification={TOKEN.upper()}"), "record_missing"),
        (
            FakeResolver(
                {TEST_UPSTREAM_HOST: [TEST_UPSTREAM_ADDRESS]},
                failing_names={RECORD_NAME},
            ),
            "lookup_failed",
        ),
        (
            FakeResolver(
                {TEST_UPSTREAM_HOST: [TEST_UPSTREAM_ADDRESS, "10.0.0.1"]},
                txt_records={RECORD_NAME: [record_value(TOKEN)]},
            ),
            "not_public",
        ),
        (FakeResolver(txt_records={RECORD_NAME: [record_value(TOKEN)]}), "not_public"),
    ],
    ids=[
        "no_record",
        "another_accounts_token",
        "token_in_another_case",
        "txt_lookup_failed",
        "private_address",
        "no_address",
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

    assert outcome == ServiceHealthOutcome(
        status=ServiceHealthStatus.FAIL,
        summary=(
            f"upstream hosts failed the domain-control check: {TEST_UPSTREAM_HOST} ({proof}); "
            f"publish a TXT record at _agent-marketplace.<host> with the value from "
            "POST /v1/provider/domain-verification"
        ),
        details={"hosts": {TEST_UPSTREAM_HOST: proof}},
    )


async def test_every_host_is_checked_and_only_the_failing_ones_are_named() -> None:
    resolver = _resolver(record_value(TOKEN))
    resolver.addresses[OTHER_HOST] = [TEST_UPSTREAM_ADDRESS]

    outcome = await check_domain_control(
        resolver=resolver,
        token=TOKEN,
        hosts={TEST_UPSTREAM_HOST, OTHER_HOST},
    )

    assert outcome.status is ServiceHealthStatus.FAIL
    assert outcome.summary is not None
    assert outcome.summary.startswith(
        f"upstream hosts failed the domain-control check: {OTHER_HOST} (record_missing);",
    )
    assert outcome.details == {
        "hosts": {OTHER_HOST: "record_missing", TEST_UPSTREAM_HOST: "verified"},
    }


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
