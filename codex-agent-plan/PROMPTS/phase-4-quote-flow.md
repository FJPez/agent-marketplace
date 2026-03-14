# Branch Handoff: `feat/quote-flow`

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

- feat/quote-flow

## Objective

Implement the quote flow so consumers can request a short-lived commercial quote for a specific service endpoint and request payload. This branch should create a stable commercial contract for later invoke and payment flows.

## In scope

- `quotes` model/table owned by this branch
- quote creation route
- request canonicalisation or normalisation
- request hash generation and binding
- quote expiry handling
- binding quotes to current revision and change token
- quote validation service logic
- tests for quote creation and quote validity rules

## Required implementation details

- quote must bind to the request payload via a stable request hash
- quote must bind to the current contract state using revision and/or change token
- keep route handlers thin
- place business rules in services
- place persistence in repositories
- keep names short and meaningful
- do not add unrelated refactors
- design the quote validation path so later invoke/payment branches can reuse it

## Out of scope

- actual invoke execution
- payment verification or settlement
- x402 request handling
- ledger logic
- advanced pricing models beyond current free/fixed-price assumptions
- broad discovery changes unrelated to quoting

## Tests required

- quote creation route tests
- request hash consistency tests
- quote expiry tests
- stale revision or change-token tests where applicable
- mismatch rejection tests for altered payloads
- repository tests for quote persistence where appropriate

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. quote model and persistence
2. request hash and quote service logic
3. route wiring
4. tests and cleanup

## Acceptance criteria

- consumers can create quotes for valid services/endpoints
- quotes bind correctly to request hash and contract state
- expired or stale quotes are rejected by the validation logic
- tests pass
- no invoke/payment/ledger flow is introduced

## Report back with

- summary of changes
- files changed
- migrations added
- tests run
- any follow-up tasks or blockers
