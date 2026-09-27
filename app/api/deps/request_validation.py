from typing import Annotated

from fastapi import Depends, Request

from app.core.lifespan import get_resources
from app.core.request_validation import RequestValidationPool


def get_request_validation_pool(request: Request) -> RequestValidationPool:
    return get_resources(request.app).request_validation_pool


RequestValidationPoolDep = Annotated[RequestValidationPool, Depends(get_request_validation_pool)]
