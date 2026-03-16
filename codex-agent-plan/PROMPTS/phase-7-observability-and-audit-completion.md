# Branch Handoff: `feat/observability-and-audit`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/02-stack-and-codebase-structure.md
- /codex-agent-plan/docs/05-x402-integration.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md
- /codex-agent-plan/PROMPTS/phase-7-provider-payout-execution.md

## Branch

- feat/observability-and-audit

## Objective

Complete observability and audit support so the platform’s core flows, including provider payout execution, can be traced, correlated, and debugged effectively.

## In scope

- richer correlation across quote, invoke, payment, ledger, and payout flows
- structured log enrichment for key operations
- business-event style audit logging where appropriate
- improved latency and error visibility
- reusable observability helpers for current flows
- tests or smoke tests for instrumentation introduced here

## Required implementation details

- build on top of the initial observability foundation from earlier phases
- include payout execution and payout reporting in the core correlated flow set
- keep route handlers thin
- avoid broad platform refactors
- focus on correlation and auditability of currently implemented flows
- keep names short and meaningful
- do not add unrelated refactors
- prefer reusable instrumentation hooks over one-off logging in route handlers

## Out of scope

- full metrics platform rollout
- dashboards
- broad OpenTelemetry redesign unless already clearly scaffolded
- unrelated business logic changes
- payout execution business rules themselves
- auth redesign

## Tests required

- correlation field tests where practical
- structured logging smoke tests
- audit-event or business-event tests if such logic is added
- targeted tests for instrumentation hooks introduced in this branch

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. correlation and audit primitives
2. instrumentation of payout and core marketplace flows
3. tests and cleanup

## Acceptance criteria

- key flows including payout execution have stronger correlation and audit visibility
- logs and/or events are more useful for debugging and support
- tests pass
- no broad observability platform rewrite is introduced

## Report back with

- summary of changes
- files changed
- tests run
- any follow-up tasks or blockers
