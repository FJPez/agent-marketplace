from enum import StrEnum


class AppEnv(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class ServiceLifecycle(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELISTED = "delisted"


class AccessMode(StrEnum):
    FREE = "free"
    PAID = "paid"


class ServiceHealthStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
