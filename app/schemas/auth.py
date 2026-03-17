from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.account import AccountResponse


class AuthNonceResponse(BaseModel):
    nonce: str


class AuthVerifyRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "message": (
                        "127.0.0.1 wants you to sign in with your Ethereum account:\n"
                        "0x1111111111111111111111111111111111111111\n\n"
                        "URI: http://127.0.0.1:8000\n"
                        "Version: 1\n"
                        "Chain ID: 84532\n"
                        "Nonce: 8db8f8134dce4a40ab79b761c392d2e4\n"
                        "Issued At: 2026-03-17T10:15:30Z"
                    ),
                    "signature": "0xabcdef1234567890",
                }
            ]
        }
    )

    message: str
    signature: str


class AuthVerifyResponse(BaseModel):
    access_token: str
    refresh_token: str
    account: AccountResponse


class AuthRefreshRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"refresh_token": "eyJhbGciOiJIUzI1NiJ9.refresh"}]}
    )

    refresh_token: str


class AuthRefreshResponse(BaseModel):
    access_token: str


class ApiKeyCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "local-demo-client",
                    "expires_at": "2026-04-01T12:00:00Z",
                }
            ]
        }
    )

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
