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

Complete the service-health branch so the marketplace has a usable health-check path and a reusable way to record and inspect health outcomes for services or endpoints.

## In scope

- concrete health-check execution path built on the earlier skeleton
- recording of health-check outcomes
- latest-known-health lookup where useful
- sanitised handling of probe/checker failures
- lightweight integration points for later publish/discovery/invoke branches
- tests for the completed health behaviour introduced here

## Required implementation details

- keep the implementation lightweight and reusable
- unexpected checker/probe errors should be handled as recorded failed checks rather than crashing the whole path
- log probe failures through the shared logging path
- store sanitised notes rather than raw stack traces in persisted health records
- prefer endpoint-level health if that fits the current model cleanly
- keep names short and meaningful
- do not add unrelated refactors
- do not turn this branch into a full monitoring platform

## Out of scope

- complex schedulers
- dashboards
- metrics platform work
- heavy background monitoring infrastructure
- hard enforcement across all marketplace flows
- unrelated provider-service or pricing logic

## Tests required

- health record persistence tests
- run-check success tests
- run-check failure tests for probe/checker exceptions
- latest-health lookup tests if added
- route tests only if this branch introduces explicit routes

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. health execution or probe service logic
2. persistence and lookup behaviour
3. tests and cleanup

## Acceptance criteria

- a health-check run can be executed and recorded
- unexpected probe failures are captured as failed health results
- later branches have a reusable health path to depend on
- tests pass
- no heavy monitoring infrastructure is introduced

## Report back with

- summary of changes
- files changed
- migrations added if any
- tests run
- any follow-up tasks or blockers
