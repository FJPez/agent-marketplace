import importlib.util
import sys

import pytest
from tests.fixtures.settings import PROJECT_ROOT

REQUEST_SCHEMAS_0008 = (
    PROJECT_ROOT / "alembic/versions/request_schemas_0008_refuse_uncompilable_request_schemas.py"
)


def test_alembic_loads_the_request_schema_revision_without_jsonschema_rs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Alembic imports every revision for every command, even where upgrade() never runs.
    monkeypatch.setitem(sys.modules, "jsonschema_rs", None)
    spec = importlib.util.spec_from_file_location("request_schemas_0008", REQUEST_SCHEMAS_0008)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert module.revision == "request_schemas_0008"
