# Branch Handoff: `feat/provider-payout-execution`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/03-data-model-and-mutability.md
- /codex-agent-plan/docs/04-api-contract.md
- /codex-agent-plan/docs/05-x402-integration.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md
- /codex-agent-plan/PROMPTS/phase-6-ledger-and-earnings.md

## Branch

- feat/provider-payout-execution

## Objective

Implement actual provider payout execution so provider earnings can move from internal ledger state into real payout attempts and payout status progression.

## In scope

- execution path for provider payouts based on ledger/earnings state
- payout initiation service logic
- use of the provider’s registered payout wallet or payout destination
- payout status lifecycle updates such as:
  - PENDING
  - READY
  - SENT
  - FAILED
- idempotent payout execution behaviour
- failure handling and retry-safe design
- persistence updates needed to support actual payout execution
- tests for payout execution behaviour introduced here

## Required implementation details

- build on top of existing ledger and earnings structures
- do not duplicate ledger calculations in payout code
- payout execution must be idempotent or strongly duplicate-safe
- keep route handlers thin if routes are added
- place payout orchestration in services
- place persistence in repositories
- keep names short and meaningful
- do not add unrelated refactors
- if provider payout wallet registration already exists, use it as the source of truth
- if payout execution interacts with an external provider/network adapter, isolate it behind a clear interface
- avoid exposing secrets or internal payout execution details in public responses

## Out of scope

- complete treasury management
- advanced payout batching
- disputes and chargebacks
- tax handling
- broad finance dashboard work
- auth redesign
- unrelated provider service redesign

## Tests required

- payout execution success tests
- payout failure tests
- duplicate or idempotent payout protection tests
- payout status transition tests
- repository tests for payout persistence updates where appropriate

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. payout execution service and adapter interface
2. payout persistence/status handling
3. integration wiring
4. tests and cleanup

## Acceptance criteria

- provider payouts can actually be executed from recognised earnings state
- payout state updates are clear and reliable
- duplicate payout risk is handled safely
- tests pass
- no broad finance redesign is introduced

## Report back with

- summary of changes
- files changed
- migrations added if any
- tests run
- any follow-up tasks or blockers
