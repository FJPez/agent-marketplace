# 08 Testing Strategy

## Test layout

```text
tests/
  conftest.py
  fixtures/
  unit/
    services/
    policies/
    utils/
    integrations/
      x402/
  integration/
    repositories/
    db/
  api/
    routes/
  e2e/
```

```

## Test levels

### Unit tests

Use for:

- mutability policy
- revision classification
- quote hash logic
- fee calculations
- moderation decisions
- payment identifier extraction and validation wrappers

### Integration tests

Use for:

- repository queries
- revision snapshot persistence
- quote persistence
- invocation persistence
- ledger persistence
- migration expectations

### API route tests

Use for:

- status codes
- auth and permission checks
- request validation
- response shape
- no upstream leakage
- `402` unpaid invoke behaviour

### End-to-end tests

Use for:

- provider publishes free service
- consumer discovers and invokes free service
- provider publishes paid service
- consumer quotes
- consumer gets `402`
- consumer retries with payment
- payment deduplicates correctly where needed
- admin suspension blocks invocation

## Branch testing rule

Every branch must include tests appropriate to its scope.

Do not defer all testing to a later branch.

## Helpful fixtures

- app fixture with overridden settings
- async DB session fixture
- provider and consumer seed fixtures
- fake facilitator client
- fake provider upstream client or server
- signed request helpers where needed

## CI rule

Every branch should pass:

- Ruff check
- format check
- mypy
- relevant tests

Integration branch should also run a core e2e smoke suite.
```
