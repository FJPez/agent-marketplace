# Agent Marketplace Backend

Backend-only marketplace where agents can publish services, discover other agents’ services, and invoke paid or free endpoints through a central platform.

## Overview

This project is a FastAPI backend for an agent-to-agent service marketplace.

Providers can:

- register as service providers
- create and manage service drafts
- define callable endpoints
- publish services with machine-readable schemas and pricing

Consumers can:

- discover available services
- inspect schemas and pricing
- request quotes
- invoke free or paid services through the platform

The platform is responsible for:

- service discovery
- publish and revision control
- quote generation
- invocation routing
- x402 payment enforcement for paid calls
- moderation and suspension
- financial records and provider earnings

## MVP scope

The MVP focuses on the core marketplace loop:

`publish -> discover -> quote -> invoke -> pay -> execute -> record`

Included in the MVP:

- provider and consumer identities
- service drafts and publishing
- endpoint definitions
- public discovery API
- fixed-price paid endpoints
- x402 payment flow
- invocation records
- ledger records
- moderation actions
- revisions and change tokens
- health checks
- testing and CI

Not included in the MVP:

- subscriptions
- usage-based billing
- public reviews
- workflow composition
- negotiation
- advanced disputes
- broad multi-chain support

## Tech stack

- Python 3.12
- FastAPI
- Pydantic v2
- pydantic-settings
- PostgreSQL
- SQLAlchemy 2.x async ORM
- Alembic
- pytest
- httpx
- uv
- Ruff
- mypy

## Repository structure

```text
app/
  main.py
  api/
    deps/
    routes/
  core/
  db/
    models/
  schemas/
  repositories/
  services/
  integrations/
    x402/
    provider_gateway/
tests/
  unit/
  integration/
  api/
    routes/
  e2e/
alembic/
codex-agent-plan/
```

## Architecture principles

- Routes stay thin.
- Business logic lives in services.
- Database access lives in repositories.
- ORM models are not exposed directly as API response models.
- All paid invocations are platform-routed.
- Provider upstreams are never exposed publicly.
- Contract-affecting changes require revision and change-token handling.
- Paid requests are only forwarded after safe payment state.

## Development setup

### Requirements

- Python 3.12
- uv
- Docker and Docker Compose
- PostgreSQL

### Initial setup

```bash
uv python install 3.12
uv sync
```

### Run the app

```bash
uv run fastapi dev app/main.py
```

### Run tests

```bash
uv run pytest
```

### Lint and format

```bash
uv run ruff check .
uv run ruff format .
uv run mypy app
```

### Run migrations

```bash
uv run alembic upgrade head
```

## Working style

This repo is designed for branch-scoped development.

Important docs:

- `AGENTS.md`
- `codex-agent-plan/README.md`
- `codex-agent-plan/docs/00-repo-bootstrap-contract.md`
- `codex-agent-plan/docs/10-definition-of-done-by-branch.md`

If you are working with a coding agent, start with those files first.

## Branch strategy

Work should be split into focused feature branches.

Examples:

- `feat/project-bootstrap`
- `feat/config-and-lifespan`
- `feat/database-core`
- `feat/shared-domain-primitives`
- `feat/auth-and-identity`

Each branch should:

- have one dominant concern
- include tests
- avoid unrelated refactors
- remain easy to review

## Testing strategy

Tests are split into:

- unit tests for pure logic and policy
- integration tests for repositories and DB-backed behaviour
- API route tests for request/response/auth behaviour
- end-to-end tests for core marketplace flows

## x402

Paid invocation will use x402-compatible payment handling.

This includes:

- `402 Payment Required` responses
- payment requirement generation
- facilitator-backed verification and settlement
- idempotency support
- payment identifier support

x402-specific code should stay inside:

```text
app/integrations/x402/
```

## Current status

This repository is currently in the early implementation phase.

The recommended implementation order is:

1. project bootstrap
2. config and lifespan
3. database core
4. shared domain primitives
5. auth and identity
6. provider services
7. pricing and publish
8. discovery
9. quote flow
10. invoke core
11. x402 payment
12. ledger and reporting

## Contributing

Before making changes:

1. read `AGENTS.md`
2. read the relevant docs in `codex-agent-plan/`
3. stay within the assigned branch scope
4. add tests with the feature
5. keep commits small and coherent
