# Branch Handoff: `feat/service-health`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/02-stack-and-codebase-structure.md
- /codex-agent-plan/docs/03-data-model-and-mutability.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md

## Branch

- feat/service-health

## Objective

Implement the service health skeleton for the marketplace. This branch should add a reusable service health check model and scaffolding so later branches can record and consume service health information without requiring a full monitoring platform now.

## In scope

- service health check model/table owned by this branch
- persistence for health-check records
- service-layer health-check scaffolding or interface
- a basic way to record service health outcomes
- tests for the behaviour introduced here

## Required implementation details

- keep the scope lightweight and reusable
- do not build a full background monitoring platform
- do not assume final publish/discovery/invoke integrations yet
- prefer a clean service interface that later branches can call into
- keep names short and meaningful
- do not add unrelated refactors

## Out of scope

- complex scheduling infrastructure
- dashboards
- metrics platform work
- deep provider monitoring
- publish/discovery/invoke enforcement beyond lightweight scaffolding
- unrelated service-domain business logic

## Tests required

- service health record persistence tests
- focused service tests for health-check recording logic
- route tests only if this branch introduces explicit routes

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. service health models and persistence
2. service-layer scaffolding
3. tests and cleanup

## Acceptance criteria

- service health records can be stored
- a reusable health-check scaffolding path exists for later branches
- tests pass
- no heavy monitoring infrastructure is introduced

## Report back with

- summary of changes
- files changed
- migrations added
- tests run
- any follow-up tasks or blockers
