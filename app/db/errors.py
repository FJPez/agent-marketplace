"""Classification of database driver errors for services translating them."""

from asyncpg import UniqueViolationError
from sqlalchemy.exc import IntegrityError


def is_unique_violation(exc: IntegrityError) -> bool:
    # SQLAlchemy's asyncpg adapter chains the driver's own exception.
    driver_error = exc.orig.__cause__ if exc.orig else None
    return isinstance(driver_error, UniqueViolationError)
