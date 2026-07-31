import pytest

from app.core.errors import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
)

TAXONOMY_CLASSES = (
    NotFoundError,
    ConflictError,
    PermissionDeniedError,
    InvalidStateError,
)


@pytest.mark.parametrize("error_class", TAXONOMY_CLASSES)
def test_taxonomy_classes_subclass_exception_directly(
    error_class: type[Exception],
) -> None:
    assert error_class.__bases__ == (Exception,)


@pytest.mark.parametrize("error_class", TAXONOMY_CLASSES)
def test_taxonomy_classes_preserve_message(error_class: type[Exception]) -> None:
    assert str(error_class("some detail message")) == "some detail message"


def test_subclass_is_caught_by_base_class() -> None:
    class MissingWidgetError(NotFoundError):
        pass

    with pytest.raises(NotFoundError):
        raise MissingWidgetError("widget missing")
