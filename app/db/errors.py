"""Classification of database driver errors for services translating them."""

from sqlalchemy.exc import IntegrityError

UNIQUE_VIOLATION_SQLSTATE = "23505"


def is_unique_violation(exc: IntegrityError) -> bool:
    sqlstate = getattr(exc.orig, "pgcode", None) or getattr(exc.orig, "sqlstate", None)
    return sqlstate == UNIQUE_VIOLATION_SQLSTATE


def unique_violation_constraint(exc: IntegrityError) -> str | None:
    """Name the unique constraint or unique index `exc` violated; None for other errors.

    Lets a service tell apart the unique keys of a table that has more than one.
    """
    if not is_unique_violation(exc):
        return None
    # SQLAlchemy's asyncpg adapter chains the driver's exception, which carries the name.
    driver_error = getattr(exc.orig, "__cause__", None)
    return getattr(driver_error, "constraint_name", None)
