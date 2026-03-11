# 02 Stack and Codebase Structure

## Stack

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
- ty

## Why this stack

- FastAPI gives strong typed API development and dependency injection.
- Pydantic v2 provides clear request/response contracts.
- PostgreSQL is a strong fit for transactional marketplace data.
- SQLAlchemy async supports the query and transaction control needed here.
- Alembic provides migration discipline.
- pytest and httpx are a strong fit for route, integration, and e2e tests.
- uv gives a fast, modern Python project workflow.

## Architecture

Use a modular monolith first.

Modules:

- auth
- providers
- services
- pricing
- discovery
- quotes
- invoke
- payments
- ledger
- moderation
- revisions
- health
- payouts
- observability

## Layering rules

### Routes

Own:

- HTTP transport concerns
- dependency wiring
- request/response mapping

Do not own:

- quote logic
- payment logic
- business rules
- direct repository orchestration across multiple domains

### Services

Own:

- business rules
- orchestration
- mutation policy
- payment flow decisions
- revision logic
- moderation checks

### Repositories

Own:

- DB queries
- persistence
- transaction-scoped access patterns as appropriate

### Schemas

Own:

- Pydantic request and response models
- validation at transport/model level

### Integrations

Own:

- x402 protocol helpers
- facilitator client
- provider gateway client
- signing

## Codebase structure

See the bootstrap contract for the canonical folder structure.

## Session and transaction handling

- Use one `AsyncSession` per request.
- Do not share the same session across unrelated concurrent tasks.
- Keep transaction boundaries explicit in services where useful.
- Avoid hidden lazy loading patterns in performance-sensitive paths.
