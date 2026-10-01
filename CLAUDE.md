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
  must not import from services.
- `app/core` contains configuration, logging, shared enums, and the shared
  application exception taxonomy. `app/core/resources.py` builds the
  process-wide resources (engine, session factory, Redis client, rate-limit
  backend) without FastAPI; the API lifespan and the worker both open them with
  `open_resources(settings)`. Add a new shared client there, not to the lifespan.
- `app/integrations` owns external protocol and provider behavior.
- `app/worker` is the background worker process (`python -m app.worker`). A
  worker loop is a `Loop` in `LOOPS` whose iteration calls a plain service
  function that processes one bounded batch. Several worker processes may run
  at once, so a loop claims its rows (`FOR UPDATE SKIP LOCKED` plus the lease
  and fence) before acting on them.

### No repository layer

`app/repositories` has been removed. Database operations live in the
`app/services` module that owns the use case.

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
- Authentication dependencies resolve the actor on their own short-lived
  session and return a plain `ActorContext`, so the route's request session
  starts with no transaction open. A dependency that reads the database for
  request-wide context does the same.
- Routes do not commit, flush, roll back, or close the session.
- Read services do not commit.
- The top-level mutation service owns the transaction and commits exactly once.
- Private helpers may `flush()` but never commit.
- Do not hold a database transaction or row lock open across external
  network I/O.
- External workflows (x402 payments, provider invocation, payouts) use the
  short-transaction convention below. It is not an ordinary CRUD pattern; do
  not force it elsewhere.

### Short transactions in external workflows

A workflow that calls something outside PostgreSQL (the facilitator, a
provider, the chain) is a sequence of short transactions, each ending in a
durable state that recovery can resume from:

1. Commit the intent: move the row to a state that records the call is about
   to happen (for example `settling` or `dispatched`), with everything needed
   to retry or reconcile it, and commit.
2. Make the external call with no transaction open.
3. Commit the outcome in a new short transaction.

Any read after the intent commit (for example to fetch data the call needs)
autobegins a new transaction, so commit or roll back after such reads, before
the external call: `session.in_transaction()` must be `False` while it runs.
For a known long-running statement, prefer a scoped `SET LOCAL
statement_timeout` over raising the global default.

The connection settings in `app/db/session.py` (statement, lock and
idle-in-transaction timeouts) are a backstop for that rule, not a design
tool.

Every state change in such a workflow is a fenced compare-and-set update:

```sql
UPDATE invocations
SET state = :next_state, ...
WHERE id = :id AND state = :expected_state AND fence = :fence
RETURNING id
```

The fencing token `fence` is incremented by the conditional update that takes
the row's lease (that update returns `RETURNING fence` instead), and the
lease holder passes it to every later update. Read the result with
`scalar_one_or_none()`: no row returned means the state moved on or another
worker took the lease, so ownership is lost: stop, perform no further side
effect (no external call, no ledger posting) and do not retry.

### Errors

- Services raise application exceptions from the shared taxonomy in
  `app/core/errors.py`: `NotFoundError`, `ConflictError`,
  `PermissionDeniedError`, `InvalidStateError`, plus feature subclasses only
  when they provide a useful message or distinct handling.
- Global FastAPI exception handlers translate application exceptions into HTTP
  responses. Services never import FastAPI HTTP types.
- Every error response is `application/problem+json` (RFC 9457), rendered by
  `app/core/problems.py`. Each exception class declares its `status_code` and
  `default_problem_type`; pass `problem_type=`, `headers=` (for example
  `Retry-After`) or `extensions=` when raising to describe a more specific
  problem.
- The OpenAPI document describes every error response as the `Problem` model,
  so a route's `responses=` only declares the status and its description.
- Application exception handlers sit inside the middleware stack, so an
  application error raised in middleware becomes a 500. Middleware must build
  and return `problem_response(...)` directly instead of raising.
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

### Constraints and indexes

- Name every new constraint and index explicitly: `name=` on
  `UniqueConstraint`, `ForeignKey` and `CheckConstraint`, the first argument of
  `Index`. Do not use `unique=True` or `index=True` for new columns. The
  metadata naming convention `uq_%(table_name)s_%(column_0_name)s` uses only
  the first column, so two unique keys on one table that start with the same
  column would get the same name. (The convention still prefixes a check
  constraint's name with `ck_<table>_`.)
- When a table has more than one unique key, a service that turns a unique
  violation into a domain error checks which key was violated with
  `unique_violation_constraint(exc)` from `app/db/errors.py`.

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
