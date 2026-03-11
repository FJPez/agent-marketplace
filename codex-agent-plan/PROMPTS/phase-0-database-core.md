# Branch Handoff: `feat/database-core`

## Read first

- `AGENTS.md`
- `README.md`
- `codex-agent-plan/README.md`
- `codex-agent-plan/docs/00-repo-bootstrap-contract.md`
- `codex-agent-plan/docs/02-stack-and-codebase-structure.md`
- `codex-agent-plan/docs/03-data-model-and-mutability.md`
- `codex-agent-plan/docs/10-definition-of-done-by-branch.md`
- `codex-agent-plan/PROMPTS/phase-0-database-core.md`

## Branch

- `feat/database-core`

## Objective

Set up the core database layer for the project using PostgreSQL, SQLAlchemy async, and Alembic. This branch should establish database connectivity, session management, migration infrastructure, and the identity baseline tables only.

## In scope

- async SQLAlchemy engine and session setup
- `app/db/session.py`
- `app/db/base.py` or equivalent metadata wiring
- Alembic configuration
- initial migrations
- identity baseline tables only:
  - `accounts`
  - `provider_profiles`
  - `consumer_profiles`
- repository-safe DB primitives that later branches can build on
- tests for migrations and basic DB usage

## Out of scope

- service marketplace tables not owned by this branch
- pricing, quote, invoke, payment, ledger, moderation, health, or revision tables
- business logic beyond DB setup and minimal model definitions

## Required implementation details

- use one async session per request pattern
- keep models in `app/db/models/`
- follow branch table ownership exactly
- keep migration naming clean and obvious
- avoid speculative schema additions for future branches
- prepare the project for later repository-layer work without implementing repositories that belong to later features

## Tests required

- migration-up test
- migration reset or rollback test where practical
- database connectivity smoke test
- basic model persistence test for identity baseline tables

## Commit guidance

Preferred commit split:

1. engine, session, and Alembic scaffolding
2. identity models
3. initial migration
4. DB tests and cleanup

## Acceptance criteria

- Alembic is configured and usable
- migrations apply cleanly
- baseline identity tables exist
- tests pass
- no out-of-scope domain tables are introduced

## Report back with

- summary of changes
- files changed
- migrations added
- tests run
- follow-up tasks or blockers
