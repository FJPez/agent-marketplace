# Branch Handoff: `feat/invoke-core`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/02-stack-and-codebase-structure.md
- /codex-agent-plan/docs/04-api-contract.md
- /codex-agent-plan/docs/05-x402-integration.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md

## Branch

- feat/invoke-core

## Objective

Implement the core invocation flow so consumers can invoke provider-defined endpoints through the marketplace gateway. This branch should support the full free execution path and establish the reusable invocation structure that later payment logic will build on.

## In scope

- `invocations` model/table owned by this branch
- `POST /v1/invoke/{service_id_or_slug}`
- `GET /v1/invocations/{invocation_id}`
- `GET /v1/invocations`
- free invoke path
- service and endpoint lookup for invocation
- request validation before forwarding
- provider upstream forwarding via the stored upstream config
- signed gateway-to-provider request handling
- invocation persistence
- idempotency key support
- timeout and upstream error mapping
- tests for invoke behaviour introduced here

## Required implementation details

- keep route handlers thin
- place orchestration in services
- place persistence in repositories
- require or strongly support idempotency keys for invoke requests
- use current quote, pricing, revision, moderation, and health structures where applicable, but do not implement payment behaviour here
- keep names short and meaningful
- do not add unrelated refactors
- never expose internal upstream configuration in public responses
- design the invoke service so the paid x402 flow can layer on top of it cleanly

## Out of scope

- payment verification
- settlement
- `402 Payment Required` protocol handling beyond any minimal placeholders required by internal design
- ledger writing
- payout logic
- advanced retry orchestration
- full payment-attempt behaviour

## Tests required

- free invoke success tests
- invocation persistence tests
- idempotency tests
- upstream timeout tests
- upstream error mapping tests
- route tests for invocation list/detail where added
- no-upstream-leakage tests

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. invocation model and persistence
2. invoke service and provider forwarding logic
3. routes
4. tests and cleanup

## Acceptance criteria

- free endpoints can be invoked end-to-end through the platform
- invocations are stored and retrievable
- idempotency is handled in a predictable way
- upstream failures are mapped cleanly
- tests pass
- no real x402 payment flow is implemented yet

## Report back with

- summary of changes
- files changed
- migrations added
- tests run
- any follow-up tasks or blockers
