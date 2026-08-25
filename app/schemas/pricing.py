from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from app.core.service_fields import normalize_currency_code

CurrencyCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    ),
    AfterValidator(normalize_currency_code),
]


class FixedPrice(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={"examples": [{"amount_minor": 250, "currency": "USD"}]},
    )

    amount_minor: Annotated[StrictInt, Field(gt=0)]
    currency: CurrencyCode
