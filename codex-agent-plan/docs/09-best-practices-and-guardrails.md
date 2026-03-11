# 09 Best Practices and Guardrails

## General coding discipline

- Keep route handlers thin.
- Keep business logic in services.
- Keep DB access in repositories.
- Keep x402 code isolated in its integration layer.
- Use explicit response models.
- Avoid accidental ORM leakage into API responses.

## Parallel work guardrails

### Freeze conventions early

Before multiple agents branch off, agree on:

- enum names
- error format
- pagination format
- timestamp naming
- migration naming
- schema naming
- route naming

### Avoid broad refactors mid-stream

Do not move folders or rename shared primitives while many branches are active unless it is absolutely required.

### Keep branches narrow

One branch should have one dominant concern.

### Use interfaces where blocked

Examples:

- facilitator client interface
- provider gateway client interface
- health probe interface
- idempotency cache interface

## Commit guardrails

- Prefer several small commits over one giant branch dump.
- Keep formatting-only changes separate where possible.
- Do not hide unrelated changes inside a feature commit.

## Merge guardrails

- Merge into the integration branch often.
- Keep the integration branch runnable.
- Do not merge a branch that breaks lint, typing, or core tests.

## Marketplace-specific guardrails

- Bind quotes to request hash.
- Require invoke idempotency.
- Support payment identifier deduplication.
- Do not forward paid requests before safe payment state.
- Do not expose provider upstreams publicly.
- Freeze executable routing while suspended.
- Make ledger entries immutable.
