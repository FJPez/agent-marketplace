# Branch Handoff: `feat/shared-domain-primitives`

Read these files first:

- `AGENTS.md`
- `README.md`
- `codex-agent-plan/README.md`
- `codex-agent-plan/docs/00-repo-bootstrap-contract.md`
- `codex-agent-plan/docs/02-stack-and-codebase-structure.md`
- `codex-agent-plan/docs/03-data-model-and-mutability.md`
- `codex-agent-plan/docs/10-definition-of-done-by-branch.md`
- `codex-agent-plan/PROMPTS/phase-0-shared-domain-primitives.md`

Work only on this branch:

- feat/shared-domain-primitives

Task:
Create the shared primitives that other branches will rely on, including common enums, shared schema building blocks, error models, helper utilities such as a request hashing contract, and a minimal shared logging foundation.

This branch should reduce duplication and stabilise conventions before multiple feature branches proceed.

In scope:

- common enums used across the application
- shared error response structures
- shared schema primitives where genuinely reusable
- request hash helper or interface contract
- common ID, timestamp, and status patterns if needed
- a minimal shared logging foundation that later branches can reuse
- unit tests for shared primitives

Required implementation details:

- keep names short and meaningful
- avoid creating vague modules like `misc`, `helpers`, or overly broad `utils`
- do not put branch-specific business rules here
- focus on primitives that will clearly be reused by multiple branches
- keep API-facing schema helpers separate from ORM concerns
- avoid speculative abstractions that are not needed yet
- do not add unrelated refactors

Logging scope for this branch:

- it is acceptable to add a small shared logging module, for example under `app/core/logging.py`
- it is acceptable to add a simple reusable logger access pattern such as `get_logger(name: str)`
- it is acceptable to define shared structured log field name constants if they are clearly reusable
- do not implement tracing, metrics, OpenTelemetry, audit pipelines, or heavy request logging middleware in this branch
- keep logging work lightweight and reusable

Out of scope:

- feature-specific schemas tied to one route group
- repositories
- route implementations
- x402 facilitator logic
- database migrations outside what is already owned elsewhere
- business logic for provider services, discovery, quote, invoke, payment, ledger, moderation, or health flows
- full observability infrastructure

Tests required:

- enum serialisation tests
- error model tests
- request hash helper tests
- any utility tests for shared primitives added here
- if the logging module contains actual logic beyond trivial wrappers, add focused unit tests for that logic

Commit guidance:
Prefer small, reviewable commits in roughly this order:

1. shared enums and status primitives
2. shared error and schema primitives
3. helper utilities and minimal logging foundation
4. tests and cleanup

Acceptance criteria:

- at least two later branches could plausibly consume these primitives
- tests pass
- no feature-specific domain logic is misplaced into shared modules
- any logging added remains lightweight and reusable rather than becoming full observability work

When done, report back with:

- summary of changes
- files changed
- tests run
- any follow-up tasks or blockers
