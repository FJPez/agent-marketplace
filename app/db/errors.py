"""Classification of database driver errors for services translating them."""

from asyncpg import UniqueViolationError
from sqlalchemy.exc import IntegrityError


def is_unique_violation(exc: IntegrityError) -> bool:
    return isinstance(exc.driver_exception, UniqueViolationError)
