# 10 Definition of Done by Branch

## `feat/project-bootstrap`

### Deliverables

- uv-managed project baseline
- Python 3.12 config
- `pyproject.toml`
- `.python-version`
- FastAPI app entrypoint
- health route
- pytest setup
- Ruff config
- ty config
- CI for lint/type/test

### Required tests

- app startup smoke test
- health route test
- config parse smoke test

### Out of scope

- domain logic
- x402 integration
- quote/invoke/payment flows

## `feat/config-and-lifespan`

### Deliverables

- typed settings
- lifespan setup
- shared client/resource startup and cleanup scaffolding

### Required tests

- settings loading tests
- lifespan startup smoke test

### Out of scope

- domain feature behaviour

## `feat/database-core`

### Deliverables

- DB connection
- session factory
- Alembic baseline
- identity tables and migrations

### Required tests

- migration-up test
- migration-down or reset test
- basic DB connectivity test

### Out of scope

- service, quote, invoke, payment tables not owned by this branch

## `feat/shared-domain-primitives`

### Deliverables

- shared enums
- common error models
- request hash helper contract
- common Pydantic primitives where needed

### Required tests

- enum serialization tests
- error model tests
- request hash helper tests

### Out of scope

- route or DB-heavy feature work

## `feat/auth-and-identity`

### Deliverables

- provider and consumer profile routes
- auth dependency baseline
- ownership context

### Required tests

- route auth tests
- profile create/read tests

### Out of scope

- service draft management

## `feat/provider-services`

### Deliverables

- draft service CRUD
- endpoint CRUD
- upstream storage
- ownership checks

### Required tests

- repository tests for service and endpoint persistence
- route tests for draft CRUD
- no-upstream-leakage tests

### Out of scope

- publish flow
- quote flow
- invoke flow

## `feat/revisions-and-change-tokens`

### Deliverables

- revision snapshots
- change-token generation
- mutation classification

### Required tests

- revision creation tests
- token bump tests
- material vs non-material change tests

### Out of scope

- discovery and payment wiring beyond what is needed for contracts

## `feat/pricing-and-publish`

### Deliverables

- pricing model support
- publish validation
- lifecycle transition to active

### Required tests

- publish validator tests
- active lifecycle transition tests

### Out of scope

- quote and invoke paths

## `feat/discovery-api`

### Deliverables

- public list/detail/schema/pricing routes
- filtering and visibility rules

### Required tests

- route tests for public visibility
- response-shape tests

### Out of scope

- quote and invoke flows

## `feat/quote-flow`

### Deliverables

- quote creation
- request hash binding
- expiry rules
- revision and token binding

### Required tests

- quote creation tests
- expiry tests
- mismatch rejection tests

### Out of scope

- provider execution
- facilitator integration

## `feat/invoke-core`

### Deliverables

- invoke endpoint
- free invoke path
- signed provider forwarding
- invocation persistence
- idempotency key support

### Required tests

- free invoke integration tests
- idempotency tests
- provider error mapping tests

### Out of scope

- x402 payment settlement logic

## `feat/x402-payment`

### Deliverables

- `402 Payment Required` path
- payment requirement builder
- payment payload handling
- facilitator adapter wiring
- payment identifier support
- payment-attempt persistence

### Required tests

- unpaid `402` tests
- payment identifier retry tests
- facilitator verify failure tests
- settle failure tests

### Out of scope

- payout reporting

## `feat/moderation-admin`

### Deliverables

- suspend, restore, delist actions
- enforcement hooks
- moderation audit records

### Required tests

- moderation route tests
- blocked invoke/publish/discovery tests

### Out of scope

- financial reporting

## `feat/service-health`

### Deliverables

- health-check records
- checker scaffolding and execution path
- publish integration if needed

### Required tests

- health persistence tests
- publish blocked on failed health where applicable

### Out of scope

- payment logic

## `feat/ledger-and-earnings`

### Deliverables

- ledger entry creation
- provider earnings summary
- ledger list routes

### Required tests

- ledger correctness tests
- aggregation tests

### Out of scope

- payout execution

## `feat/platform-guardrails`

### Deliverables

- rate limiting
- replay protection
- payload size limits

### Required tests

- rate limit tests
- replay protection tests
- oversized payload tests

### Out of scope

- unrelated route additions

## `feat/payouts-reporting`

### Deliverables

- payout records
- payout list routes
- payout preparation support

### Required tests

- payout aggregation tests
- payout status transition tests

### Out of scope

- direct external payout execution unless explicitly required

## `feat/observability-and-audit`

### Deliverables

- request and trace correlation
- business-event style logging
- audit enrichment for key flows

### Required tests

- log/trace smoke tests
- correlation field tests where practical

### Out of scope

- core business feature implementation unrelated to observability
