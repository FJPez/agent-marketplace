# Branch Handoff: `feat/moderation-admin`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/03-data-model-and-mutability.md
- /codex-agent-plan/docs/04-api-contract.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md

## Branch

- feat/moderation-admin

## Objective

Implement the moderation and admin skeleton for the marketplace. This branch should add moderation action recording and the initial admin-oriented suspend/restore/delist scaffolding without turning into full enforcement across all later marketplace flows.

## In scope

- moderation action model/table owned by this branch
- moderation action persistence
- admin-oriented moderation service scaffolding
- initial suspend, restore, and delist action structures
- route or internal service scaffolding appropriate to the current repo maturity
- tests for moderation action recording and basic moderation behaviour introduced here

## Required implementation details

- keep the scope skeletal and reusable
- do not over-build full enforcement before later marketplace branches exist
- focus on action recording, status transitions, and clear interfaces/hooks for later branches
- keep route handlers thin if routes are added
- keep names short and meaningful
- do not add unrelated refactors

## Out of scope

- full enforcement across publish, discovery, quote, invoke, and finance flows
- complex admin UI assumptions
- broad role/permission redesign
- audit/event platform work beyond the scope of moderation action persistence
- discovery or provider-service business logic

## Tests required

- moderation action persistence tests
- status transition tests if lifecycle changes are introduced here
- route tests if admin moderation routes are added in this branch
- focused service tests for moderation behaviour introduced here

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. moderation models and persistence
2. moderation service or policy scaffolding
3. routes if included
4. tests and cleanup

## Acceptance criteria

- moderation actions can be recorded cleanly
- suspend/restore/delist scaffolding exists in a reusable form
- tests pass
- the branch does not overreach into full enforcement or unrelated admin features

## Report back with

- summary of changes
- files changed
- migrations added
- tests run
- any follow-up tasks or blockers
