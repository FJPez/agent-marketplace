import logging
from typing import Final

REQUEST_ID_FIELD: Final[str] = "request_id"
ACCOUNT_ID_FIELD: Final[str] = "account_id"
SERVICE_ID_FIELD: Final[str] = "service_id"
QUOTE_ID_FIELD: Final[str] = "quote_id"
INVOCATION_ID_FIELD: Final[str] = "invocation_id"
ERROR_CODE_FIELD: Final[str] = "error_code"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
