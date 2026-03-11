# 05 x402 Integration

## Goal

Use x402 to enforce paid HTTP invocation for marketplace routes without letting x402-specific concerns take over unrelated application layers.

## Integration principles

- Keep x402-specific code inside `app/integrations/x402/`.
- Keep route handlers thin.
- Let marketplace services decide when payment is required.
- Only forward paid requests to providers after safe payment state.

## Expected x402 usage

Use x402 for:

- payment challenge and response flow
- payment requirement generation
- facilitator integration
- payment identifier support

Do not use x402 as the owner of:

- quote validity
- request hash comparison
- revision and change-token validation
- moderation policy
- ledger behaviour

## Python and FastAPI notes

The project should be compatible with the official Python x402 support for FastAPI.

Integration layer responsibilities:

- payment requirement creation
- payment payload parsing
- facilitator client interface
- payment identifier extraction and validation
- mapping protocol outcomes into application payment states

## Payment identifier

Support both:

- route-level invoke idempotency key
- x402 payment identifier

These solve related but different retry problems.

Recommended storage on payment attempt records:

- invocation id
- quote id
- payment identifier
- payment requirement payload
- payment payload
- verify outcome
- settle outcome
- facilitator reference

## Facilitator adapter

Create an interface such as `FacilitatorClient` so that:

- test environments can use a stub or fake
- production can use a real facilitator
- payment orchestration does not depend on one concrete vendor path

## Request flow

1. Consumer invokes paid endpoint.
2. Marketplace checks lifecycle, moderation, quote, request hash, revision, and change token.
3. If no valid payment details are present, return `402 Payment Required`.
4. Advertise payment identifier support.
5. On retry, extract the payment identifier.
6. Deduplicate if already safely processed.
7. Verify and settle through facilitator logic.
8. Only then call provider upstream.
9. Persist payment attempt and invocation outcome.
