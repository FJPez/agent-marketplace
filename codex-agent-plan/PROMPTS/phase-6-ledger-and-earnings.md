# Branch Handoff: `feat/ledger-and-earnings`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/03-data-model-and-mutability.md
- /codex-agent-plan/docs/04-api-contract.md
- /codex-agent-plan/docs/05-x402-integration.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md

## Branch

- feat/ledger-and-earnings

## Objective

Implement the financial ledger and provider earnings layer so paid invokes create durable accounting records and providers can inspect what they have earned.

## In scope

- `ledger_entries` model/table owned by this branch
- ledger persistence for paid execution outcomes
- entry types such as:
  - charge
  - platform fee
  - provider earning
  - refund placeholder if needed by current design
- provider earnings aggregation
- provider ledger endpoint
- provider earnings summary endpoint
- service-layer logic for writing ledger records
- tests for ledger and earnings behaviour introduced here

## Required implementation details

- keep ledger entries immutable after creation
- write financial records in a central service path rather than scattering writes across routes
- build on top of existing paid invoke and payment-attempt outcomes
- keep route handlers thin
- place persistence in repositories
- place aggregation and accounting rules in services
- keep names short and meaningful
- do not add unrelated refactors
- do not introduce payout execution in this branch

## Out of scope

- payout execution
- payout scheduling
- advanced refund/dispute workflows
- new payment models
- broad finance dashboards
- unrelated auth or provider-service changes

## Tests required

- ledger entry persistence tests
- accounting/aggregation tests
- provider earnings summary route tests
- provider ledger route tests
- tests that paid execution results in expected ledger records
- immutability tests if relevant logic is added

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. ledger model and persistence
2. ledger service and earnings aggregation logic
3. provider finance routes
4. tests and cleanup

## Acceptance criteria

- paid invokes produce ledger records
- provider earnings can be aggregated and retrieved
- providers can inspect ledger history
- tests pass
- no payout execution logic is introduced

## Report back with

- summary of changes
- files changed
- migrations added
- tests run
- any follow-up tasks or blockers
