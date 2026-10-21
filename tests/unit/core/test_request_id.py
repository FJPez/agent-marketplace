from uuid import UUID

import pytest

from app.core.request_id import resolve_request_id


@pytest.mark.parametrize(
    "request_id",
    ["incoming-request-id", "0af7651916cd43dd8448eb211c80319c", "trace.id:1_2", "a" * 128],
)
def test_resolve_request_id_keeps_a_valid_client_value(request_id: str) -> None:
    assert resolve_request_id(request_id) == request_id


@pytest.mark.parametrize(
    "request_id",
    [None, "", "   ", "a" * 129, "abc\ndef", 'abc","level":"CRITICAL', "abc def", "café"],
    ids=["missing", "empty", "blank", "too_long", "newline", "json", "space", "non_ascii"],
)
def test_resolve_request_id_generates_a_uuid_for_a_missing_or_invalid_value(
    request_id: str | None,
) -> None:
    generated = resolve_request_id(request_id)

    assert str(UUID(generated)) == generated
