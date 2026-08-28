import pytest

from app.services.ledger_service import split_paid_invocation_amount


@pytest.mark.parametrize(
    ("amount_minor", "expected"),
    [
        (500, (50, 450)),
        (5, (0, 5)),
        (4_999_999, (499_999, 4_500_000)),
        (0, (0, 0)),
    ],
)
def test_split_paid_invocation_amount_floors_the_platform_fee(
    amount_minor: int,
    expected: tuple[int, int],
) -> None:
    assert split_paid_invocation_amount(amount_minor) == expected
