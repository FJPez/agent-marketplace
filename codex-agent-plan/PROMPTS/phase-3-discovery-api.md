# Branch Handoff: `feat/discovery-api`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/02-stack-and-codebase-structure.md
- /codex-agent-plan/docs/04-api-contract.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md

## Branch

- feat/discovery-api

## Objective

Implement the public discovery API so consumers can list and inspect active services without exposing internal-only provider configuration.

## In scope

- public service list route
- public service detail route
- public schema route
- public pricing route
- active and visible service filtering
- public DTOs and mapping logic
- tests for discovery visibility and response shape

## Required implementation details

- only expose public, active, discoverable services
- never expose upstream configuration or internal provider secrets
- keep route handlers thin
- place query and filtering behaviour in repositories/services as appropriate
- use explicit Pydantic response models
- keep names short and meaningful
- do not add unrelated refactors

## Out of scope

- quote flow
- invoke flow
- x402 integration
- ledger logic
- admin moderation enforcement beyond what is already available in current state
- advanced search features such as semantic search

## Tests required

- service list route tests
- service detail route tests
- schema route tests
- pricing route tests
- visibility/filtering tests
- no-internal-field-leakage tests

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. public schemas and DTO mapping
2. discovery repository/service logic
3. routes
4. tests and cleanup

## Acceptance criteria

- consumers can list and inspect active services
- only public-safe fields are exposed
- internal upstream data is not leaked
- tests pass
- no quote, invoke, or payment logic is introduced

## Report back with

- summary of changes
- files changed
- tests run
- any follow-up tasks or blockers
