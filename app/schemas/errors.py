from pydantic import BaseModel


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str


class ApiError(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ApiError
