# Codex Agent Plan Pack

This folder contains implementation planning documents for a backend-only agent marketplace built with:

- FastAPI
- PostgreSQL
- Pydantic v2
- SQLAlchemy async
- Alembic
- x402

The documents are targeted at coding agents and are designed to support branch-scoped execution with strong guardrails for parallel work.

## Start here

Read these in order:

1. `AGENTS.md`
2. `README.md`
3. `docs/00-repo-bootstrap-contract.md`
4. `docs/01-overview-and-goals.md`
5. `docs/10-definition-of-done-by-branch.md`

Then read the docs most relevant to the assigned branch.

## Document map

### Core repo setup

- `docs/00-repo-bootstrap-contract.md`
  - Repo conventions
  - uv usage
  - Python version
  - folder structure
  - Ruff and ty expectations
  - CI and test command expectations

### Product and architecture

- `docs/01-overview-and-goals.md`
  - Product goals
  - MVP scope
  - included vs excluded features

- `docs/02-stack-and-codebase-structure.md`
  - stack choices
  - architecture
  - folder structure
  - layering rules

### Domain model and contract

- `docs/03-data-model-and-mutability.md`
  - tables
  - ownership
  - mutability policy
  - revision rules

- `docs/04-api-contract.md`
  - route groups
  - endpoint expectations
  - request flow

- `docs/05-x402-integration.md`
  - x402 approach
  - facilitator model
  - payment identifier
  - Python/FastAPI integration notes

### Delivery plan

- `docs/06-branch-and-workstream-plan.md`
  - branch map
  - workstreams
  - parallelisation guidance

- `docs/07-phase-by-phase-implementation.md`
  - exact implementation order
  - phase breakdown
  - what can run in parallel

### Quality and guardrails

- `docs/08-testing-strategy.md`
  - test levels
  - test layout
  - required branch testing

- `docs/09-best-practices-and-guardrails.md`
  - coding practices
  - merge rules
  - conflict avoidance
  - delivery discipline

### Branch handoff and completion

- `docs/10-definition-of-done-by-branch.md`
  - branch-by-branch deliverables
  - required tests
  - out-of-scope guidance

## Prompts

- Phase 0
  - `PROMPTS/phase-0-bootstrap.md`
  - `PROMPTS/phase-0-config-and-lifespan.md`
  - `PROMPTS/phase-0-database-core.md`
  - `PROMPTS/phase-0-shared-domain-primitives.md`
- Phase 1
  - `PROMPTS/phase-1-auth-and-identity.md`
  - `PROMPTS/phase-1-observability-and-audit-initial.md`
- Phase 2
  - `PROMPTS/phase-2-moderation-admin.md`
  - `PROMPTS/phase-2-parallel-coordination.md`
  - `PROMPTS/phase-2-provider-services.md`
  - `PROMPTS/phase-2-service-health.md`
- Phase 3
  - `PROMPTS/phase-3-discovery-api.md`
  - `PROMPTS/phase-3-parallel-coordination.md`
  - `PROMPTS/phase-3-pricing-and-publish.md`
  - `PROMPTS/phase-3-revisions-and-change-tokens.md`
- Phase 4
  - `PROMPTS/phase-4-moderation-admin-completion.md`
  - `PROMPTS/phase-4-parallel-coordination.md`
  - `PROMPTS/phase-4-quote-flow.md`
  - `PROMPTS/phase-4-service-health-completion.md`
- Phase 5
  - `PROMPTS/phase-5-coordination.md`
  - `PROMPTS/phase-5-invoke-core.md`
  - `PROMPTS/phase-5-x402-payment.md`

## Suggested usage

For each coding agent:

1. Assign exactly one branch.
2. Give the agent `AGENTS.md`.
3. Give the agent this README.
4. Give the agent the branch-specific docs and prompt.
5. Tell the agent to stay within branch scope.
