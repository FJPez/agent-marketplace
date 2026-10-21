"""Classification of database driver errors for services translating them."""

from asyncpg import UniqueViolationError
from sqlalchemy.exc import IntegrityError


def is_unique_violation(exc: IntegrityError) -> bool:
    return isinstance(exc.driver_exception, UniqueViolationError)


def unique_violation_constraint(exc: IntegrityError) -> str | None:
    """Name the unique constraint or unique index `exc` violated; None for other errors."""
    driver_error = exc.driver_exception
    if not isinstance(driver_error, UniqueViolationError):
        return None
    # asyncpg sets the fields of a server error dynamically, so they are read through as_dict.
    return driver_error.as_dict().get("constraint_name")
