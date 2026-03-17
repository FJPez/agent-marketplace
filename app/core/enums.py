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


class PricingModelType(StrEnum):
    FREE = "free"
    FIXED_PER_CALL = "fixed_per_call"


class InvocationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class InvocationFailureReason(StrEnum):
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_TRANSPORT = "upstream_transport"
    UPSTREAM_RESPONSE = "upstream_response"


class LedgerEntryType(StrEnum):
    CHARGE = "charge"
    PLATFORM_FEE = "platform_fee"
    PROVIDER_EARNING = "provider_earning"
    REFUND = "refund"


class PayoutStatus(StrEnum):
    READY = "ready"
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class PayoutFailureCode(StrEnum):
    EXECUTOR_ERROR = "executor_error"
    INVALID_AMOUNT = "invalid_amount"
    WALLET_NOT_CONFIGURED = "wallet_not_configured"


class ServiceHealthStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
