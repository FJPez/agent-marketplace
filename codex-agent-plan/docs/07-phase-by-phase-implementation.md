# 07 Phase-by-Phase Implementation

## Phase 0, sequential

1. `feat/project-bootstrap`
2. `feat/config-and-lifespan`
3. `feat/database-core`
4. `feat/shared-domain-primitives`

Deliver:

- FastAPI skeleton
- uv setup
- Python 3.12 baseline
- pydantic-settings config
- lifespan-managed resources
- SQLAlchemy async session setup
- Alembic
- pytest
- Ruff
- mypy
- CI

## Phase 1, parallel

- `feat/auth-and-identity`
- `feat/observability-and-audit` initial

Deliver:

- auth baseline
- provider and consumer profile creation
- ownership context
- request logging and request IDs

## Phase 2, parallel

- `feat/provider-services`
- `feat/moderation-admin` skeleton
- `feat/service-health` skeleton

Deliver:

- service draft CRUD
- endpoint CRUD
- upstream storage
- moderation action scaffolding
- health-check scaffolding

## Phase 3, parallel

- `feat/revisions-and-change-tokens`
- `feat/pricing-and-publish`
- `feat/discovery-api`

Deliver:

- revision snapshots
- change-token generation
- publish validation
- active lifecycle transition
- public discovery routes

## Phase 4, parallel

- `feat/quote-flow`
- `feat/service-health` completion
- `feat/moderation-admin` completion

Deliver:

- quote creation
- request hash binding
- expiry rules
- health gate for publishing where needed
- moderation enforcement hooks

## Phase 5, mostly sequential

1. `feat/invoke-core`
2. `feat/x402-payment`

Deliver:

- free invoke path
- signed provider forwarding
- invocation persistence
- `402 Payment Required` path
- facilitator integration
- payment identifier support
- paid invoke execution path

## Phase 6, parallel

- `feat/ledger-and-earnings`
- `feat/platform-guardrails`

Deliver:

- ledger entry creation
- provider earnings views
- rate limits
- replay protection
- payload protection

## Phase 7, parallel

- `feat/payouts-reporting`
- `feat/observability-and-audit` completion

Deliver:

- payout records and reporting
- trace and metric enrichment
- business-event style auditability
