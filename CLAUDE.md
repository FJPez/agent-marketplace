# AGENTS.md

This repository is a backend-only agent marketplace built with FastAPI,
PostgreSQL, Pydantic v2, SQLAlchemy async, and x402.

All coding agents must follow this file before making changes.

Common commands live in `Makefile`. Understand them and use them when
appropriate.

## Architecture

The canonical request flow is:

```text
FastAPI route
    -> receives request schemas and dependencies
    -> calls a plain service function
    -> service uses an explicitly supplied AsyncSession
    -> service owns business rules, SQLAlchemy operations, and the transaction
    -> route returns a public Pydantic response schema
```

Layer responsibilities:

- `app/api/routes` owns HTTP inputs, dependencies, status declarations, and
  response declarations.
- `app/api/deps` owns request-scoped resource acquisition: database sessions,
  authenticated accounts, shared request context.
- `app/services` owns use cases, business rules, SQLAlchemy queries, ORM state
  changes, and transaction boundaries.
- `app/db/models` contains SQLAlchemy ORM models only.
- `app/schemas` contains Pydantic request and response models only. Schemas
  must not import from services or repositories.
- `app/core` contains configuration, logging, shared enums, and the shared
  application exception taxonomy.
- `app/integrations` owns external protocol and provider behavior.

### Repository layer removal (in progress)

`app/repositories` is legacy and is being removed in verified vertical slices.
Until removal is complete:

- Do not add new code that uses or extends `app/repositories`.
- New database operations go directly in the relevant `app/services` module.
- Do not recreate the abstraction under another name: no `crud`, `dao`,
  `data_access`, ORM manager classes, generic service base classes, or one-line
  query wrappers around SQLAlchemy.

### Services

- Services are plain async module-level functions with keyword-only arguments
  and an explicitly supplied `AsyncSession`.
- Introduce a class only when several operations genuinely share meaningful
  state or collaborators.
- `Depends()` and other FastAPI types do not belong in service signatures.
  Dependencies resolve values at the API boundary and pass ordinary Python
  values into services.
- Keep each query beside the use case that owns it. Extract shared query code
  only after concrete cross-service reuse is demonstrated.

### Transactions

- The session dependency owns session lifetime only. It never commits.
- Routes do not commit, flush, roll back, or close the session.
- Read services do not commit.
- The top-level mutation service owns the transaction and commits exactly once.
- Private helpers may `flush()` but never commit.
- Do not hold a database transaction open across external network I/O.
- External workflows (x402 payments, provider invocation, payouts) may use
  multiple short transactions with explicit durable states, idempotency, and
  safe retries. This is not an ordinary CRUD pattern; do not force it
  elsewhere.

### Errors

- Services raise application exceptions from the shared taxonomy in
  `app/core/errors.py`: `NotFoundError`, `ConflictError`,
  `PermissionDeniedError`, `InvalidStateError`, plus feature subclasses only
  when they provide a useful message or distinct handling.
- Global FastAPI exception handlers translate application exceptions into HTTP
  responses. Services never import FastAPI HTTP types.
- Use route-local `HTTPException` only for errors genuinely local to one HTTP
  operation.
- Do not wrap every route in `try`/`except` and do not log the same exception
  at every layer.

### Schemas

- Use separate Pydantic models for separate API states: `XCreate`, `XUpdate`,
  `XRead`.
- Response schemas use `ConfigDict(from_attributes=True)` and contain only
  public fields. Never expose ORM models as the API contract.
- Queries must eagerly load everything a response needs. Pydantic conversion
  must not trigger async lazy-loading after the service returns.

## Primary goals

- Keep branch scope narrow and easy to review.
- Keep the integration branch runnable.
- Prefer small, coherent commits.
- Follow the documented folder structure and layering rules.
- Write tests as part of the feature, not after it.
- Do not implement beyond the assigned branch scope unless the task explicitly
  requires it.

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
- If blocked by another branch, code against an interface or stub and document
  the dependency clearly.

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
Do not use `typing.cast()` unless `ty` fails without it. Prefer explicit
typing, narrower code paths, or better-typed intermediates before adding a
cast.

## Testing rules

Every branch must ship with tests appropriate to its scope.

Test layers:

- unit tests for pure logic and policy
- PostgreSQL-backed integration tests for DB-backed services
- API route tests for request/response/auth behaviour
- e2e tests only for critical end-to-end flows

Do not rely only on route-level tests.

Test behavior at the narrowest meaningful boundary:

- Do not mock `AsyncSession`, SQLAlchemy statements, or result chains. Query
  and persistence behavior belongs in PostgreSQL-backed integration tests.
- Do not assert that one internal layer called another. Assert observable
  behavior.
- Test plain service functions directly. Use FastAPI dependency overrides in
  API tests.

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
3. services
4. routes/schemas
5. tests
6. docs/fixes

Avoid mixing these in one commit:

- tooling changes with domain logic
- migrations with unrelated refactors
- route changes with large formatting-only noise

Before creating a commit, run the branch verification needed for the touched
files, including `uv run ruff format .` and `uv run ruff check .`.

## Integration branch rules

- The integration branch must stay runnable.
- Every merge should pass lint, type checks, and core tests.
- Do not merge a branch that leaves the project in a partially broken state.

## x402-specific rules

- Keep x402 code inside `app/integrations/x402/`.
- Do not let x402-specific logic leak across unrelated modules.
- Support both invoke-level idempotency and x402 payment identifier
  idempotency.
- Never forward a paid request upstream before safe payment state is confirmed.
