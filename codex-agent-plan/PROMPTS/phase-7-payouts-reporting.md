# Branch Handoff: `feat/payouts-reporting`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/03-data-model-and-mutability.md
- /codex-agent-plan/docs/04-api-contract.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md
- /codex-agent-plan/PROMPTS/phase-7-provider-payout-execution.md

## Branch

- feat/payouts-reporting

## Objective

Implement payout reporting so providers can inspect payout history, per-currency
summaries, and payout statuses using the actual request-based payout execution
model as the source of truth.

## In scope

- `payouts` model/table owned by this branch if not already present
- payout record persistence and retrieval
- provider payout list endpoint
- provider payout request replay visibility where useful
- payout status visibility
- per-currency payout summary or aggregation support where useful
- tests for payout reporting behaviour introduced here

## Required implementation details

- build on top of actual payout execution behaviour, not a placeholder payout model
- keep this branch focused on payout records and reporting, not execution
- keep route handlers thin
- place reporting and aggregation logic in services
- place persistence in repositories
- keep names short and meaningful
- do not add unrelated refactors
- ensure providers only see their own payout records
- avoid leaking raw executor error text or transfer internals in provider responses

## Out of scope

- payout execution logic itself
- advanced finance dashboards
- unrelated earnings redesign
- auth redesign
- broad observability changes

## Tests required

- payout record persistence tests
- payout list route tests
- payout status filtering or retrieval tests
- provider-scoping tests
- aggregation/summary tests where implemented

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. payout record persistence and read models
2. payout reporting/aggregation logic
3. provider payout routes
4. tests and cleanup

## Acceptance criteria

- payout records can be retrieved by the correct provider
- payout statuses are visible and reliable
- mixed-currency payout summaries are complete
- tests pass
- no actual payout execution logic is duplicated in this branch

## Report back with

- summary of changes
- files changed
- migrations added if any
- tests run
- any follow-up tasks or blockers
