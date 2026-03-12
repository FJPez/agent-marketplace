class IdentityConflictError(Exception):
    pass


class IdentityNotFoundError(Exception):
    def __init__(self, *, profile_type: str, account_id: int) -> None:
        self.profile_type = profile_type
        self.account_id = account_id
        super().__init__(f"{profile_type} profile not found for account {account_id}")


class IdentityValidationError(Exception):
    pass
