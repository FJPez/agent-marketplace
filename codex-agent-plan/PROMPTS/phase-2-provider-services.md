# Branch Handoff: `feat/provider-services`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/02-stack-and-codebase-structure.md
- /codex-agent-plan/docs/03-data-model-and-mutability.md
- /codex-agent-plan/docs/04-api-contract.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md

## Branch

- feat/provider-services

## Objective

Implement provider-owned service draft management. This branch should add the core service draft, endpoint, tag, and upstream configuration model so providers can define services that will later be publishable and discoverable.

## In scope

- service draft creation
- provider-owned service read/list/update behaviour
- service tags
- service endpoints
- hidden upstream configuration
- provider ownership checks using the current identity/account model
- required schemas, services, repositories, and route handlers for this branch
- tests for service draft and endpoint management behaviour

## Required implementation details

- use the current Phase 1 identity behaviour as the source of truth
- provider-owned resources should resolve ownership through the authenticated account and linked provider profile
- keep route handlers thin
- place business logic in services
- place persistence in repositories
- keep upstream configuration internal-only and never expose it in public response models
- keep names short and meaningful
- do not add unrelated refactors
- follow the mutability policy for draft-stage fields from the planning docs

## Out of scope

- publish flow
- pricing models
- revisions and change tokens
- public discovery
- quote flow
- invoke flow
- x402 integration
- ledger logic
- moderation enforcement beyond what is strictly needed to avoid conflicting design

## Tests required

- provider service create route tests
- provider service list/read/update tests
- endpoint create/update tests
- ownership enforcement tests
- no-upstream-leakage response tests
- repository tests for service and endpoint persistence where appropriate

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. service, tag, endpoint, and upstream models or persistence pieces owned by this branch
2. repositories and service-layer logic
3. schemas and routes
4. tests and cleanup

## Acceptance criteria

- authenticated providers can create and manage draft services
- services can have endpoints and tags
- upstream config is stored but not exposed publicly
- ownership is enforced correctly
- tests pass
- no publish/discovery/payment logic is introduced

## Report back with

- summary of changes
- files changed
- migrations added
- tests run
- any follow-up tasks or blockers
