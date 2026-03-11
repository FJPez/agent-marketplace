import math

import pytest

from app.core.request_hash import hash_request_body


def test_hash_request_body_is_stable_for_equivalent_dict_order() -> None:
    left = {"service": "translate", "payload": {"text": "hello", "target": "fr"}}
    right = {"payload": {"target": "fr", "text": "hello"}, "service": "translate"}

    assert hash_request_body(left) == hash_request_body(right)


def test_hash_request_body_changes_when_array_order_changes() -> None:
    left = {"steps": ["draft", "publish"]}
    right = {"steps": ["publish", "draft"]}

    assert hash_request_body(left) != hash_request_body(right)


def test_hash_request_body_handles_unicode_text() -> None:
    payload = {"message": "naïve café"}
    expected_hash = "cf55682842bb3e48563a4138e3a193026e7e91ebaf9e0bbffeb20a9c6df5e5cb"

    assert hash_request_body(payload) == expected_hash


def test_hash_request_body_accepts_typed_mapping_payload() -> None:
    payload: dict[str, object] = {"message": "hello"}

    assert (
        hash_request_body(payload)
        == "9b2d43affbf49a367028df2e1414f84c0e099ac98c3d54a8a80157fd7771af25"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"value": {1, 2, 3}},
        {"value": math.nan},
    ],
)
def test_hash_request_body_rejects_non_json_compatible_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="JSON-compatible"):
        hash_request_body(payload)
