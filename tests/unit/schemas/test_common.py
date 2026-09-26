from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas.common import DisplayName, Id, Timestamp


class CommonModel(BaseModel):
    id: Id
    created_at: Timestamp


class DisplayNameModel(BaseModel):
    display_name: DisplayName


def test_common_aliases_accept_valid_values() -> None:
    model = CommonModel.model_validate(
        {
            "id": 1,
            "created_at": "2026-03-11T12:30:00Z",
        }
    )

    assert model.id == 1
    assert model.created_at == datetime(2026, 3, 11, 12, 30, tzinfo=UTC)


def test_display_name_alias_strips_surrounding_whitespace() -> None:
    model = DisplayNameModel.model_validate({"display_name": "  Alpha Agent  "})

    assert model.display_name == "Alpha Agent"


@pytest.mark.parametrize(
    ("payload", "field_name"),
    [
        ({"id": 0, "created_at": "2026-03-11T12:30:00Z"}, "id"),
        ({"id": 1, "created_at": "2026-03-11T12:30:00"}, "created_at"),
        ({"id": True, "created_at": "2026-03-11T12:30:00Z"}, "id"),
        ({"id": False, "created_at": "2026-03-11T12:30:00Z"}, "id"),
        ({"id": "1", "created_at": "2026-03-11T12:30:00Z"}, "id"),
        ({"id": 1.0, "created_at": "2026-03-11T12:30:00Z"}, "id"),
    ],
)
def test_common_aliases_reject_invalid_values(
    payload: dict[str, object],
    field_name: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CommonModel.model_validate(payload)

    assert exc_info.value.errors()[0]["loc"] == (field_name,)


@pytest.mark.parametrize(
    "display_name",
    [
        "",
        "   ",
        "x" * 256,
    ],
)
def test_display_name_alias_rejects_invalid_values(display_name: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        DisplayNameModel.model_validate({"display_name": display_name})

    assert exc_info.value.errors()[0]["loc"] == ("display_name",)
