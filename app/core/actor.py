from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActorContext:
    account_id: int
    is_admin: bool = False
