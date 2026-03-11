from pydantic import BaseModel

from app.core.enums import AccessMode, AppEnv, ServiceLifecycle


class EnumModel(BaseModel):
    env: AppEnv
    lifecycle: ServiceLifecycle
    access_mode: AccessMode


def test_shared_enums_parse_from_strings() -> None:
    model = EnumModel.model_validate(
        {
            "env": "test",
            "lifecycle": "active",
            "access_mode": "paid",
        }
    )

    assert model.env is AppEnv.TEST
    assert model.lifecycle is ServiceLifecycle.ACTIVE
    assert model.access_mode is AccessMode.PAID


def test_shared_enums_serialize_as_values() -> None:
    model = EnumModel(
        env=AppEnv.PROD,
        lifecycle=ServiceLifecycle.SUSPENDED,
        access_mode=AccessMode.FREE,
    )

    assert model.model_dump(mode="json") == {
        "env": "prod",
        "lifecycle": "suspended",
        "access_mode": "free",
    }
