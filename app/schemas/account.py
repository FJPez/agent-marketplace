from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.common import DisplayName, Id, Timestamp


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Id
    wallet_address: str
    account_type: str
    is_admin: bool
    display_name: DisplayName
    created_at: Timestamp
    updated_at: Timestamp


class AccountUpdateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"display_name": "Marketplace Demo"}]}
    )

    display_name: DisplayName | None = None


class WalletChangeInitiateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"wallet_address": "0x2222222222222222222222222222222222222222"}]
        }
    )

    wallet_address: str


class WalletChangeInitiateResponse(BaseModel):
    nonce: str
    expires_at: datetime


class WalletChangeConfirmRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "message": "127.0.0.1 wants you to confirm a wallet change...",
                    "signature": "0xabcdef1234567890",
                }
            ]
        }
    )

    message: str
    signature: str


class WalletChangeConfirmResponse(BaseModel):
    access_token: str
    refresh_token: str
    account: AccountResponse
