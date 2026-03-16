from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActorContext:
    account_id: int
    is_admin: bool = False
    account_type: str = "human"
    auth_method: str = "jwt"
    wallet_address: str = ""
