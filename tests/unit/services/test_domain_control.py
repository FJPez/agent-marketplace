import pytest
from tests.helpers.dns import FakeResolver

from app.services.domain_control import MAX_HOSTS, VerificationRecord, check_domain_control

RECORD = VerificationRecord(label="_agent-marketplace", value="agent-marketplace-verification=a")
HOST = "api.example.com"
RECORD_NAME = f"{RECORD.label}.{HOST}"
PUBLIC_ADDRESS = "93.184.215.14"


def _resolver(
    *,
    addresses: list[str] | None = None,
    records: list[str] | None = None,
    failing: tuple[str, ...] = (),
) -> FakeResolver:
    return FakeResolver(
        {HOST: [PUBLIC_ADDRESS] if addresses is None else addresses},
        txt_records={RECORD_NAME: [RECORD.value] if records is None else records},
        failing_names=failing,
    )


@pytest.mark.parametrize(
    ("resolver", "problems"),
    [
        pytest.param(_resolver(), [], id="proved"),
        pytest.param(
            _resolver(records=[f" {RECORD.value} "]),
            [],
            id="record_with_surrounding_spaces",
        ),
        pytest.param(
            _resolver(records=[]),
            [f"{HOST} must carry the TXT record {RECORD_NAME}"],
            id="no_record",
        ),
        pytest.param(
            _resolver(records=["agent-marketplace-verification=b"]),
            [f"{HOST} must carry the TXT record {RECORD_NAME}"],
            id="another_accounts_record",
        ),
        pytest.param(
            _resolver(addresses=[]),
            [f"{HOST} must resolve only to public IPv4 addresses"],
            id="no_address",
        ),
        pytest.param(
            _resolver(addresses=[PUBLIC_ADDRESS, "10.0.0.5"]),
            [f"{HOST} must resolve only to public IPv4 addresses"],
            id="private_address_beside_a_public_one",
        ),
        pytest.param(
            _resolver(addresses=["169.254.169.254"]),
            [f"{HOST} must resolve only to public IPv4 addresses"],
            id="metadata_address",
        ),
        pytest.param(
            _resolver(failing=(RECORD_NAME,)),
            [f"the DNS lookup for {HOST} failed; retry"],
            id="lookup_failed",
        ),
    ],
)
async def test_check_domain_control_names_each_problem(
    resolver: FakeResolver,
    problems: list[str],
) -> None:
    assert await check_domain_control(resolver=resolver, record=RECORD, hosts={HOST}) == problems


async def test_check_domain_control_reports_only_the_hosts_that_fail() -> None:
    resolver = _resolver()
    resolver.addresses["other.example.com"] = [PUBLIC_ADDRESS]

    problems = await check_domain_control(
        resolver=resolver,
        record=RECORD,
        hosts={HOST, "other.example.com"},
    )

    assert problems == [
        f"other.example.com must carry the TXT record {RECORD.label}.other.example.com",
    ]


async def test_check_domain_control_refuses_too_many_hosts_before_any_lookup() -> None:
    hosts = {f"host{number}.example.com" for number in range(MAX_HOSTS + 1)}

    problems = await check_domain_control(
        resolver=FakeResolver(failing_names=hosts),
        record=RECORD,
        hosts=hosts,
    )

    assert problems == [f"a service's upstreams can name at most {MAX_HOSTS} hosts"]
