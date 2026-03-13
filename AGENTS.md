# AGENTS.md

This repository contains planning and implementation guidance for a backend-only agent marketplace built with FastAPI, PostgreSQL, Pydantic, SQLAlchemy async, and x402.

All coding agents must follow this file before making changes.

## Primary goals

- Keep branch scope narrow and easy to review.
- Keep the integration branch runnable.
- Prefer small, coherent commits.
- Follow the documented folder structure and layering rules.
- Write tests as part of the feature, not after it.
- Do not implement beyond the assigned branch scope unless the task explicitly requires it.

## Read order before coding

For any task, read in this order:

1. `AGENTS.md`
2. `README.md`
3. `codex-agent-plan/README.md`
4. `codex-agent-plan/docs/00-repo-bootstrap-contract.md`
5. `codex-agent-plan/docs/10-definition-of-done-by-branch.md`
6. The branch-specific prompt in `codex-agent-plan/PROMPTS/` if one exists
7. Any branch-specific docs referenced by the prompt

## Stack baseline

- Python 3.12
- uv for dependency and environment management
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

## Repository structure rules

Use this structure unless explicitly told otherwise:

```text
app/
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
```

## Layering rules

- `api/routes` should stay thin.
- Route handlers should validate transport-level concerns and delegate business logic.
- `services` own business rules and orchestration.
- `repositories` own database access.
- `db/models` contains SQLAlchemy ORM models only.
- `schemas` contains Pydantic request/response models only.
- `integrations/x402` owns x402-specific protocol and facilitator code.
- Do not expose ORM models directly as public API response models.

## Branch discipline

- One branch should have one dominant concern.
- Do not mix unrelated features in one branch.
- Do not refactor unrelated modules while implementing a feature branch.
- If blocked by another branch, code against an interface or stub and document the dependency clearly.

## Migration ownership

Only the owning branch should create the migration for its table family.

Table ownership:

- `feat/database-core`
  - `accounts`
  - `provider_profiles`
  - `consumer_profiles`

- `feat/provider-services`
  - `services`
  - `service_tags`
  - `service_endpoints`
  - `provider_upstreams`

- `feat/pricing-and-publish`
  - `pricing_models`

- `feat/revisions-and-change-tokens`
  - `service_revisions`

- `feat/quote-flow`
  - `quotes`

- `feat/invoke-core`
  - `invocations`

- `feat/x402-payment`
  - `payment_attempts`

- `feat/ledger-and-earnings`
  - `ledger_entries`

- `feat/payouts-reporting`
  - `payouts`

- `feat/moderation-admin`
  - `moderation_actions`

- `feat/service-health`
  - `service_health_checks`

## Naming conventions

Use standard Python naming conventions:

- modules, files, functions, variables: `snake_case`
- classes: `PascalCase`
- constants: `UPPER_SNAKE_CASE`

Names should be short, meaningful, and specific.
Avoid vague names like:

- `data`
- `helper`
- `manager`
- `utils`
  unless the context makes the role explicit.

## Linting and typing expectations

Ruff rule families should include at least:

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

Expected selective ignores:

- `ANN101`
- `ANN102`
- `D203`
- `D212`

Any broader ignore set should be justified in the branch.

Use ty on application code and tests.
Do not use `typing.cast()` unless `ty` fails without it. Prefer explicit typing, narrower code paths, or better-typed intermediates before adding a cast.
Run `uv run ruff format .` regularly while working and again before each commit to avoid format-only CI failures.
Run `uv run ruff check .` after formatting so lint verification is based on the formatted tree.

## Testing rules

Every branch must ship with tests appropriate to its scope.

Test layers:

- unit tests for pure logic and policy
- integration tests for repositories and DB-backed services
- API route tests for request/response/auth behaviour
- e2e tests only for critical end-to-end flows

Do not rely only on route-level tests.

## Commit preferences

Commits should be:

- small
- coherent
- reviewable
- ordered logically
- made incrementally as the work progresses, not all at the end

Prefer 3 to 6 commits for a medium branch rather than one large dump.

Good commit split example:

1. tooling/config
2. models/migrations
3. services/repositories
4. routes/schemas
5. tests
6. docs/fixes

Avoid mixing these in one commit:

- tooling changes with domain logic
- migrations with unrelated refactors
- route changes with large formatting-only noise

Before creating a commit, run the branch verification needed for the touched files, including `uv run ruff format .` and `uv run ruff check .`.

## Integration branch rules

- The integration branch must stay runnable.
- Every merge should pass lint, type checks, and core tests.
- Do not merge a branch that leaves the project in a partially broken state.

## x402-specific rules

- Keep x402 code inside `app/integrations/x402/`.
- Do not let x402-specific logic leak across unrelated modules.
- Support both invoke-level idempotency and x402 payment identifier idempotency.
- Never forward a paid request upstream before safe payment state is confirmed.

## Reporting back after a task

When a task is complete, report:

- summary of changes
- files changed
- tests run
- anything intentionally left out
- follow-up risks or blockers
