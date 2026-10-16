from enum import StrEnum


class AppEnv(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    TEST = "test"
    PROD = "prod"


class ServiceLifecycle(StrEnum):
    """A service is a draft until it is published, then active.

    Suspension and delisting are moderation states, derived from the service's
    moderation actions (`app/services/moderation.py`), not lifecycle values.
    """

    DRAFT = "draft"
    ACTIVE = "active"


class AccessMode(StrEnum):
    FREE = "free"
    PAID = "paid"


class ServiceHealthStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
