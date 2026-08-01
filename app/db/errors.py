"""Classification of database driver errors for services translating them."""

from sqlalchemy.exc import IntegrityError

UNIQUE_VIOLATION_SQLSTATE = "23505"


def is_unique_violation(exc: IntegrityError) -> bool:
    sqlstate = getattr(exc.orig, "pgcode", None) or getattr(exc.orig, "sqlstate", None)
    return sqlstate == UNIQUE_VIOLATION_SQLSTATE
