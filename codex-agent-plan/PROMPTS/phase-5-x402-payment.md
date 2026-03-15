# Branch Handoff: `feat/x402-payment`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/04-api-contract.md
- /codex-agent-plan/docs/05-x402-integration.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md
- /codex-agent-plan/PROMPTS/phase-5-invoke-core.md

## Branch

- feat/x402-payment

## Objective

Implement the x402-backed paid invocation flow so paid endpoints return `402 Payment Required`, accept payment payloads, verify and settle through a facilitator path, and only then forward execution to the provider.

## In scope

- `payment_attempts` model/table owned by this branch
- `402 Payment Required` response handling for paid invokes
- payment requirement generation
- payment payload intake and parsing
- payment identifier support
- facilitator client or adapter integration
- verify/settle orchestration
- payment-attempt persistence
- paid invoke execution path completion using the invoke-core branch
- tests for x402 payment behaviour introduced here

## Required implementation details

- build on top of the existing invoke-core structure rather than duplicating it
- keep x402-specific logic inside the integration and payment service layers
- keep route handlers thin
- support both invoke idempotency and payment-identifier-based deduplication where applicable
- do not forward paid requests upstream before safe payment state is confirmed
- bind payment flow to current quote/request-hash/contract assumptions where applicable
- keep names short and meaningful
- do not add unrelated refactors

## Out of scope

- payout reporting
- ledger writing
- advanced refunds or disputes
- non-x402 payment models
- broad auth redesign
- advanced multi-chain expansion beyond the current branch needs

## Tests required

- unpaid paid-endpoint invoke returns `402 Payment Required`
- payment requirement generation tests
- payment identifier handling tests
- facilitator verify failure tests
- facilitator settle failure tests
- successful paid invoke integration tests
- payment-attempt persistence tests
- deduplication or retry-behaviour tests where implemented

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. payment-attempt model and persistence
2. x402 integration helpers and facilitator adapter
3. payment orchestration on top of invoke-core
4. tests and cleanup

## Acceptance criteria

- paid endpoints return a `402 Payment Required` flow
- valid payment payloads can complete the paid invoke path
- provider execution only happens after safe payment confirmation
- payment attempts are recorded
- tests pass
- no ledger or payout reporting logic is introduced

## Report back with

- summary of changes
- files changed
- migrations added
- tests run
- any follow-up tasks or blockers
