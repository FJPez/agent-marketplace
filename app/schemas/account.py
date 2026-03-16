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
    display_name: DisplayName | None = None


class WalletChangeInitiateRequest(BaseModel):
    wallet_address: str


class WalletChangeInitiateResponse(BaseModel):
    nonce: str
    expires_at: datetime


class WalletChangeConfirmRequest(BaseModel):
    message: str
    signature: str


class WalletChangeConfirmResponse(BaseModel):
    access_token: str
    refresh_token: str
    account: AccountResponse
