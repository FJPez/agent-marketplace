# Branch Handoff: `feat/auth-and-identity`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/02-stack-and-codebase-structure.md
- /codex-agent-plan/docs/04-api-contract.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md

## Branch

- feat/auth-and-identity

## Objective

Implement the application’s initial auth and identity layer so the backend can recognise provider and consumer actors and expose the baseline identity routes. This branch should establish actor context and role-aware access patterns without moving into provider service management yet.

## In scope

- baseline auth dependency or auth context extraction
- provider profile creation route
- provider self-read route
- provider self-update route
- consumer profile creation route
- typed request and response schemas for these routes
- ownership or actor context that later branches can depend on
- services and repositories required for this identity layer
- tests for auth and identity route behaviour

## Required implementation details

- keep route handlers thin
- place business logic in services
- place persistence in repositories
- use explicit Pydantic request and response models
- keep auth context typed and reusable
- do not hard-wire assumptions that would block later admin/provider/consumer role expansion
- keep names short and meaningful
- do not add unrelated refactors

## Out of scope

- provider service draft CRUD
- endpoint management
- publish, quote, invoke, payment, ledger, moderation, discovery, or health flows
- advanced auth product features not required for the baseline branch

## Tests required

- provider create route tests
- provider self-read route tests
- provider self-update route tests
- consumer create route tests
- auth dependency tests
- permission or actor-context tests for protected behaviour

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. auth dependency and actor context
2. provider and consumer schemas/services/repositories
3. routes
4. tests and cleanup

## Acceptance criteria

- provider and consumer identity routes work
- auth context is reusable by later branches
- tests pass
- no service-draft or marketplace logic is introduced

## Report back with

- summary of changes
- files changed
- tests run
- any follow-up tasks or blockers
