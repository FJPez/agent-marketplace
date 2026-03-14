# Branch Handoff: `feat/pricing-and-publish`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/03-data-model-and-mutability.md
- /codex-agent-plan/docs/04-api-contract.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md

## Branch

- feat/pricing-and-publish

## Objective

Implement pricing support and the publish flow so provider-defined draft services can become active marketplace services with clear commercial configuration.

## In scope

- `pricing_models` model/table owned by this branch
- support for `FREE` and `FIXED_PER_CALL`
- service-layer pricing logic needed for current MVP
- publish validation rules
- draft-to-active publish transition
- publish prerequisites and error handling
- tests for pricing and publish behaviour

## Required implementation details

- keep pricing scope limited to `FREE` and `FIXED_PER_CALL`
- use clear validation around required fields for paid vs free endpoints
- ensure publish only succeeds when prerequisites are satisfied
- keep names short and meaningful
- keep route handlers thin
- do not add unrelated refactors
- integrate cleanly with current provider-services structures

## Out of scope

- public discovery
- quote flow
- invoke flow
- x402 integration
- ledger logic
- advanced pricing models such as subscriptions or usage-based billing

## Tests required

- pricing model persistence tests
- publish validator tests
- lifecycle transition tests for draft to active
- tests for publish failure when prerequisites are missing

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. pricing model and persistence
2. pricing service logic
3. publish validation and lifecycle transition
4. tests and cleanup

## Acceptance criteria

- services can be configured as free or fixed-price
- valid draft services can be published
- invalid services fail publish with clear errors
- tests pass
- no discovery, quote, or payment flow logic is introduced

## Report back with

- summary of changes
- files changed
- migrations added
- tests run
- any follow-up tasks or blockers
