# Branch Handoff: `feat/moderation-admin`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/03-data-model-and-mutability.md
- /codex-agent-plan/docs/04-api-contract.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md

## Branch

- feat/moderation-admin

## Objective

Complete the moderation-admin branch so moderation actions are not only recorded but can also be applied in a reusable way by the marketplace’s current flows.

## In scope

- completion of suspend, restore, and delist behaviour
- shared moderation enforcement hooks or service checks
- clear moderation state handling for the objects that currently exist in the repo
- route or service completion needed for current admin moderation operations
- tests for enforceable moderation behaviour introduced here

## Required implementation details

- keep enforcement reusable and central rather than scattering checks throughout routes
- keep the implementation focused on currently existing flows and objects
- do not overbuild a large admin platform
- suspend should represent stronger enforcement than delist
- delist should primarily affect public visibility
- restore should reverse prior moderation state where appropriate
- keep names short and meaningful
- do not add unrelated refactors

## Out of scope

- complex appeals workflow
- admin dashboards
- policy engine automation
- finance restrictions across future branches
- full enforcement in not-yet-implemented quote/invoke/payment flows
- unrelated discovery or provider-service redesign

## Tests required

- moderation state transition tests
- suspend/restore/delist behaviour tests
- enforcement hook tests for currently existing flows
- route tests if admin moderation endpoints are present in this branch

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. moderation state/enforcement service logic
2. route or service completion
3. tests and cleanup

## Acceptance criteria

- suspend, restore, and delist behaviour is implemented in a reusable way
- current flows can consult moderation state through shared hooks or services
- tests pass
- the branch does not overreach into a full admin platform or future not-yet-built flows

## Report back with

- summary of changes
- files changed
- migrations added if any
- tests run
- any follow-up tasks or blockers
