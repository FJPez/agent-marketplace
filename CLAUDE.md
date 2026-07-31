# AGENTS.md

This repository contains planning and implementation guidance for a backend-only agent marketplace built with FastAPI, PostgreSQL, Pydantic, SQLAlchemy async, and x402.

All coding agents must follow this file before making changes.

Command commands live in `Makefile`. Understand them and use them when appropriate.

## Primary goals

- Keep branch scope narrow and easy to review.
- Keep the integration branch runnable.
- Prefer small, coherent commits.
- Follow the documented folder structure and layering rules.
- Write tests as part of the feature, not after it.
- Do not implement beyond the assigned branch scope unless the task explicitly requires it.

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

## Branch discipline

- One branch should have one dominant concern.
- Do not mix unrelated features in one branch.
- Do not refactor unrelated modules while implementing a feature branch.
- If blocked by another branch, code against an interface or stub and document the dependency clearly.

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

Use ty on application code and tests.
Do not use `typing.cast()` unless `ty` fails without it. Prefer explicit typing, narrower code paths, or better-typed intermediates before adding a cast.

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
