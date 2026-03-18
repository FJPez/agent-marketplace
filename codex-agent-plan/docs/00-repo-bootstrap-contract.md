# 00 Repo Bootstrap Contract

This document defines the repository-level conventions that coding agents must follow when bootstrapping or extending the codebase.

## Required stack baseline

- Python 3.12
- uv for environment and dependency management
- FastAPI
- Pydantic v2
- pydantic-settings
- SQLAlchemy 2.x async ORM
- Alembic
- PostgreSQL
- pytest
- httpx
- Ruff
- ty

## Environment and dependency management

Use uv.

Required files:

- `pyproject.toml`
- `.python-version`
- `uv.lock`

Expected local workflow:

- `uv sync`
- `uv run ...`

Example commands:

```bash
uv python install 3.12
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv run ty check
uv run alembic upgrade head
uv run fastapi dev app/main.py
```

## Python version

Use Python 3.12.

Create:

- `.python-version` containing `3.12`

## Folder structure

Use this structure:

```text
app/
  main.py
  api/
    deps/
    routes/
      account.py
      account_wallet.py
      auth.py
      provider_services.py
      discovery.py
      quotes.py
      invoke.py
      admin.py
      finance.py
      health.py
  core/
    config.py
    lifespan.py
    errors.py
    logging.py
    security.py
    idempotency.py
  db/
    base.py
    session.py
    models/
      account.py
      api_key.py
      service.py
      service_tag.py
      service_endpoint.py
      provider_upstream.py
      pricing_model.py
      service_revision.py
      quote.py
      invocation.py
      payment_attempt.py
      ledger_entry.py
      payout.py
      moderation_action.py
      service_health_check.py
      wallet_change_log.py
  schemas/
    auth.py
    common.py
    account.py
    service.py
    discovery.py
    quote.py
    invoke.py
    admin.py
    finance.py
    errors.py
  repositories/
    account_repo.py
    api_key_repo.py
    service_repo.py
    quote_repo.py
    invocation_repo.py
    payment_attempt_repo.py
    ledger_entry_repo.py
    payout_repo.py
    pricing_model_repo.py
    provider_upstream_repo.py
    service_endpoint_repo.py
    service_health_check_repo.py
    service_revision_repo.py
    wallet_change_log_repo.py
  services/
    account_service.py
    api_key_service.py
    auth_resolution_service.py
    auth_service.py
    provider_draft_service.py
    provider_endpoint_service.py
    publish_service.py
    discovery_service.py
    quote_service.py
    invoke_service.py
    payment_service.py
    moderation_service.py
    revision_service.py
    ledger_service.py
    payout_service.py
    health_service.py
    publish_readiness.py
    service_health_service.py
    wallet_change_service.py
  integrations/
    x402/
      facilitator_client.py
      models.py
      payment_requirements.py
      payment_identifier.py
      resource_server.py
    payouts/
      executor.py
    provider_gateway/
      client.py
      signing.py
tests/
  conftest.py
  fixtures/
  unit/
  integration/
    repositories/
    db/
  api/
    routes/
  e2e/
alembic/
```

## Coding conventions

### Naming

Use standard Python naming conventions:

- files: `snake_case`
- functions: `snake_case`
- classes: `PascalCase`
- constants: `UPPER_SNAKE_CASE`

Names should be short and meaningful.
Prefer:

- `quote_repo`
- `invoke_service`
- `provider_id`
- `health_probe`

Avoid:

- `common_utils`
- `data_handler`
- `misc`
- `stuff`

### Layering

- Routes stay thin.
- Services own orchestration.
- Repositories own DB access.
- ORM models are not public response models.
- Pydantic schemas define request/response contracts.

## Ruff configuration

At minimum, include these rule families:

- `E`
- `F`
- `I`
- `B`
- `UP`
- `SIM`
- `N`
- `RUF`
- `ANN`

Recommended additions:

- `ASYNC`
- `A`
- `C4`
- `DTZ`
- `G`
- `ICN`
- `ISC`
- `LOG`
- `PIE`
- `PT`
- `PTH`
- `RET`
- `SLOT`
- `T20`
- `TC`

Suggested ignore list to start with:

- `ANN101`
- `ANN102`
- `D203`
- `D212`

Broader ignores should be justified in the relevant branch.

## ty expectations

Run ty against `app` and `tests`.

Prefer:

- typed function signatures
- typed settings objects
- typed service interfaces
- typed repository method returns

Avoid:

- pervasive `Any`
- silent `type: ignore` without explanation

## Testing conventions

Tests live under top-level `tests/`, not beside production files.

Mirror structure where helpful:

- `tests/api/routes/test_discovery.py`
- `tests/unit/services/test_quote_service.py`
- `tests/integration/repositories/test_quote_repo.py`

## CI requirements

Set up CI to run on pull requests and pushes to main working branches.

Minimum CI jobs:

1. Ruff check
2. Ruff format check
3. ty
4. pytest

CI should:

- install Python 3.12
- install uv
- sync dependencies
- run quality checks
- run tests

## Bootstrap branch expectations

The first bootstrap branch should include:

- FastAPI app skeleton
- typed settings
- lifespan setup
- uv-based project setup
- basic Docker Compose for PostgreSQL
- Alembic setup
- pytest setup
- Ruff config
- ty config
- one health endpoint
- CI for lint/type/test

## Commit-splitting preferences

Do not make one giant bootstrap commit.

Preferred split:

1. project metadata and uv setup
2. app skeleton and config
3. database and Alembic setup
4. tooling config for Ruff and ty
5. tests and health route
6. CI

Commits should be easy to review and logically separated.

## Out of scope for bootstrap

Do not implement:

- business domain tables beyond the core identity baseline if not assigned
- quote logic
- invoke logic
- x402 integration
- ledger logic
- admin enforcement beyond scaffolding if not assigned
