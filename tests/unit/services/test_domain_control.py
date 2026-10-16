from uuid import UUID

import pytest
from tests.helpers.dns import FakeResolver

from app.services.domain_control import MAX_HOSTS, RECORD_LABEL, check_domain_control, record_value

TOKEN = UUID("0b6a5c1e-2f3d-4e5f-8a9b-0c1d2e3f4a5b")
HOST = "api.example.com"
RECORD_NAME = f"{RECORD_LABEL}.{HOST}"
PUBLIC_ADDRESS = "93.184.215.14"


def _resolver(
    *,
    addresses: list[str] | None = None,
    records: list[str] | None = None,
    failing: tuple[str, ...] = (),
) -> FakeResolver:
    return FakeResolver(
        {HOST: [PUBLIC_ADDRESS] if addresses is None else addresses},
        txt_records={RECORD_NAME: [record_value(TOKEN)] if records is None else records},
        failing_names=failing,
    )


@pytest.mark.parametrize(
    ("resolver", "problems"),
    [
        pytest.param(_resolver(), [], id="proved"),
        pytest.param(
            _resolver(records=[f" {record_value(TOKEN)} "]),
            [],
            id="record_with_surrounding_spaces",
        ),
        pytest.param(
            _resolver(records=[]),
            [f"{HOST} must carry the TXT record {RECORD_NAME}"],
            id="no_record",
        ),
        pytest.param(
            _resolver(records=[record_value(UUID(int=1))]),
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
    assert await check_domain_control(resolver=resolver, token=TOKEN, hosts={HOST}) == problems


async def test_check_domain_control_reports_only_the_hosts_that_fail() -> None:
    resolver = _resolver()
    resolver.addresses["other.example.com"] = [PUBLIC_ADDRESS]

    problems = await check_domain_control(
        resolver=resolver,
        token=TOKEN,
        hosts={HOST, "other.example.com"},
    )

    assert problems == [
        f"other.example.com must carry the TXT record {RECORD_LABEL}.other.example.com",
    ]


async def test_check_domain_control_refuses_too_many_hosts_before_any_lookup() -> None:
    hosts = {f"host{number}.example.com" for number in range(MAX_HOSTS + 1)}

    problems = await check_domain_control(
        resolver=FakeResolver(failing_names=hosts),
        token=TOKEN,
        hosts=hosts,
    )

    assert problems == [f"a service's upstreams can name at most {MAX_HOSTS} hosts"]
