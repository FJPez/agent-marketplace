# Branch Handoff: `feat/shared-domain-primitives`

## Read first

- `AGENTS.md`
- `README.md`
- `codex-agent-plan/README.md`
- `codex-agent-plan/docs/00-repo-bootstrap-contract.md`
- `codex-agent-plan/docs/02-stack-and-codebase-structure.md`
- `codex-agent-plan/docs/03-data-model-and-mutability.md`
- `codex-agent-plan/docs/10-definition-of-done-by-branch.md`
- `codex-agent-plan/PROMPTS/phase-0-shared-domain-primitives.md`

## Branch

- `feat/shared-domain-primitives`

## Objective

Create the shared primitives that other branches will rely on, including common enums, shared schema building blocks, error models, and helper utilities such as request hashing contracts. This branch should reduce duplication and stabilise conventions before multiple feature branches proceed.

## In scope

- common enums used across the application
- shared error response structures
- shared schema primitives where genuinely reusable
- request hash helper or interface contract
- common ID, timestamp, and status patterns if needed
- unit tests for shared primitives

## Out of scope

- feature-specific schemas tied to one route group
- repositories
- route implementations
- x402 facilitator logic
- database migrations outside what is already owned elsewhere

## Required implementation details

- keep names short and meaningful
- avoid creating “misc” or overly generic helper modules
- do not put branch-specific business rules here
- focus on primitives that will clearly be reused by multiple branches
- keep API-facing schema helpers separate from ORM concerns

## Tests required

- enum serialisation tests
- error model tests
- request hash helper tests
- any utility tests for shared primitives added here

## Commit guidance

Preferred commit split:

1. shared enums and status primitives
2. shared error and schema primitives
3. helper utilities and tests

## Acceptance criteria

- at least two later branches could plausibly consume these primitives
- tests pass
- no feature-specific domain logic is misplaced into shared modules

## Report back with

- summary of changes
- files changed
- tests run
- follow-up tasks or blockers
