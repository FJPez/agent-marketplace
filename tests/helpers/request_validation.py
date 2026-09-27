from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_validation_worker import body_refusal, schema_refusal


class InProcessRequestValidationPool:
    """A RequestValidationPool that compiles and validates in the test's own process.

    It applies the workers' own checks, without their isolation or deadlines, so tests that
    are not about those start no worker process.
    """

    async def check_schema(self, schema_json: str) -> str | None:
        return schema_refusal(schema_json)

    async def validate(self, schema_json: str, body: bytes) -> str | None:
        return body_refusal(schema_json, body)


IN_PROCESS_REQUEST_VALIDATION_POOL = InProcessRequestValidationPool()


class TransactionWatchingPool(InProcessRequestValidationPool):
    """Answers as the in-process pool does and records `session.in_transaction()` at
    every compile.

    Proves a save compiles its request schema with no transaction open on `session`, or
    does not compile it at all.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.in_transaction_during_compiles: list[bool] = []

    async def check_schema(self, schema_json: str) -> str | None:
        self.in_transaction_during_compiles.append(self._session.in_transaction())
        return await super().check_schema(schema_json)
