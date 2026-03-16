from datetime import datetime

from pydantic import BaseModel

from app.schemas.account import AccountResponse


class AuthNonceResponse(BaseModel):
    nonce: str


class AuthVerifyRequest(BaseModel):
    message: str
    signature: str


class AuthVerifyResponse(BaseModel):
    access_token: str
    refresh_token: str
    account: AccountResponse


class AuthRefreshRequest(BaseModel):
    refresh_token: str


class AuthRefreshResponse(BaseModel):
    access_token: str


class ApiKeyCreateRequest(BaseModel):
    name: str | None = None
    expires_at: datetime | None = None


class ApiKeyResponse(BaseModel):
    id: int
    name: str | None
    key_prefix: str
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreateResponse(ApiKeyResponse):
    api_key: str
