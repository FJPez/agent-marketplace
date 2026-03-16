# Branch Handoff: `feat/platform-guardrails`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/05-x402-integration.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md

## Branch

- feat/platform-guardrails

## Objective

Implement the platform guardrails needed to make the marketplace safer and more resilient, especially around invoke and payment flows.

## In scope

- rate limiting
- payload size limits
- replay protection
- stronger duplicate submission handling
- protective behaviour around retries where appropriate
- reusable service or middleware hooks for guardrail enforcement
- tests for guardrail behaviour introduced here

## Required implementation details

- keep the implementation focused on practical protection for current flows
- prefer reusable enforcement points over duplicated route-level logic
- keep route handlers thin
- align replay and duplicate protection with existing idempotency and payment-identifier behaviour
- choose limits that are configurable rather than hard-coded where appropriate
- keep names short and meaningful
- do not add unrelated refactors
- avoid turning this branch into a full anti-abuse platform

## Out of scope

- advanced bot scoring
- fraud/risk engines
- WAF-style infrastructure
- complex adaptive throttling
- unrelated observability work
- payout or ledger feature work
- broad auth redesign

## Tests required

- rate-limit behaviour tests
- oversized payload rejection tests
- replay protection tests
- duplicate submission handling tests
- tests for integration with current invoke/payment paths where applicable

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. configurable guardrail primitives
2. enforcement hooks or middleware
3. invoke/payment integration points
4. tests and cleanup

## Acceptance criteria

- the platform applies practical request guardrails to current critical flows
- replay and duplicate behaviour are handled more safely
- payload limits and rate limits are enforced predictably
- tests pass
- no unrelated platform redesign is introduced

## Report back with

- summary of changes
- files changed
- tests run
- any follow-up tasks or blockers
