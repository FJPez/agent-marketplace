# 06 Branch and Workstream Plan

## Workstreams

### A. Foundations

- project bootstrap
- config and lifespan
- DB baseline
- shared primitives

### B. Identity and provider CRUD

- auth and identity
- provider service draft management

### C. Contract and discovery

- revisions and change tokens
- pricing and publish
- discovery API

### D. Commerce execution

- quote flow
- invoke core
- x402 payment

### E. Operations

- moderation admin
- service health
- platform guardrails

### F. Finance and supportability

- ledger and earnings
- payouts reporting
- observability and audit

## Branch map

### Foundations

- `feat/project-bootstrap`
- `feat/config-and-lifespan`
- `feat/database-core`
- `feat/shared-domain-primitives`

### Identity and provider CRUD

- `feat/auth-and-identity`
- `feat/provider-services`

### Contract and discovery

- `feat/revisions-and-change-tokens`
- `feat/pricing-and-publish`
- `feat/discovery-api`

### Commerce execution

- `feat/quote-flow`
- `feat/invoke-core`
- `feat/x402-payment`

### Operations

- `feat/moderation-admin`
- `feat/service-health`
- `feat/platform-guardrails`

### Finance and supportability

- `feat/ledger-and-earnings`
- `feat/payouts-reporting`
- `feat/observability-and-audit`

## Parallelisation guidance

- Do not parallelise the initial repo baseline too early.
- Parallelise once shared conventions and base structure are stable.
- Keep one dominant concern per branch.
- Use interfaces or stubs when blocked by a neighbouring branch.

## Good agent allocation

- Agent 1: foundations
- Agent 2: identity and provider CRUD
- Agent 3: contract and discovery
- Agent 4: quote, invoke, x402 payment
- Agent 5: moderation, finance, payouts, guardrails
- Agent 6: health and observability
