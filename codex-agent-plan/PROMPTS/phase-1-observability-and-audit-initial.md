# Branch Handoff: `feat/observability-and-audit`

## Read first

- /AGENTS.md
- /README.md
- /codex-agent-plan/README.md
- /codex-agent-plan/docs/00-repo-bootstrap-contract.md
- /codex-agent-plan/docs/02-stack-and-codebase-structure.md
- /codex-agent-plan/docs/09-best-practices-and-guardrails.md
- /codex-agent-plan/docs/10-definition-of-done-by-branch.md

## Branch

- feat/observability-and-audit

## Objective

Implement the initial observability foundation for the application. This branch should add request correlation and structured logging basics that improve debugging and support later auditability, without turning into full tracing or full observability infrastructure.

## In scope

- request ID generation or propagation
- structured request logging foundation
- basic error logging hooks
- lightweight correlation fields that later branches can reuse
- app wiring needed for this initial observability support
- focused tests for the behaviour introduced here

## Required implementation details

- keep the implementation lightweight
- reuse the shared logging foundation already introduced in shared-domain-primitives
- avoid broad refactors to application startup or route structure
- prefer reusable middleware or clearly isolated hooks where appropriate
- keep names short and meaningful
- do not add unrelated refactors

## Out of scope

- OpenTelemetry
- distributed tracing
- metrics exporters
- dashboards
- business-event audit pipelines
- full domain audit trail implementation
- x402-specific telemetry
- finance-specific reporting

## Tests required

- request ID presence or propagation tests
- structured logging smoke tests where practical
- error logging hook tests if the implementation contains testable logic

## Commit guidance

Prefer small, reviewable commits in roughly this order:

1. request correlation primitives and wiring
2. structured logging hooks or middleware
3. tests and cleanup

## Acceptance criteria

- requests have a usable correlation or request ID path
- the app has a basic structured logging foundation beyond simple print-style logging
- tests pass
- no full tracing or heavy observability infrastructure is introduced

## Report back with

- summary of changes
- files changed
- tests run
- any follow-up tasks or blockers
