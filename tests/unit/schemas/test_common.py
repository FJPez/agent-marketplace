from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas.common import Id, RequestHash, Timestamp


class CommonModel(BaseModel):
    id: Id
    created_at: Timestamp
    request_hash: RequestHash


def test_common_aliases_accept_valid_values() -> None:
    model = CommonModel.model_validate(
        {
            "id": 1,
            "created_at": "2026-03-11T12:30:00Z",
            "request_hash": "a" * 64,
        }
    )

    assert model.id == 1
    assert model.created_at == datetime(2026, 3, 11, 12, 30, tzinfo=UTC)
    assert model.request_hash == "a" * 64


@pytest.mark.parametrize(
    ("payload", "field_name"),
    [
        (
            {
                "id": 0,
                "created_at": "2026-03-11T12:30:00Z",
                "request_hash": "a" * 64,
            },
            "id",
        ),
        (
            {
                "id": 1,
                "created_at": "2026-03-11T12:30:00",
                "request_hash": "a" * 64,
            },
            "created_at",
        ),
        (
            {
                "id": 1,
                "created_at": "2026-03-11T12:30:00Z",
                "request_hash": "not-a-hash",
            },
            "request_hash",
        ),
    ],
)
def test_common_aliases_reject_invalid_values(
    payload: dict[str, object],
    field_name: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CommonModel.model_validate(payload)

    assert exc_info.value.errors()[0]["loc"] == (field_name,)
