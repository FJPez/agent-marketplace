# 03 Data Model and Mutability

## Table groups

### Identity

- `accounts`
- `provider_profiles`
- `consumer_profiles`

Identity profile notes:

- `provider_profiles` includes `display_name` for provider self-service identity reads and updates
- `consumer_profiles` includes `display_name` for consumer identity creation

### Service definition

- `services`
- `service_tags`
- `service_endpoints`
- `provider_upstreams`
- `pricing_models`

### Contract tracking

- `service_revisions`

### Commerce

- `quotes`
- `invocations`
- `payment_attempts`
- `ledger_entries`
- `payouts`

### Operations

- `moderation_actions`
- `service_health_checks`

## Table ownership by branch

- `feat/database-core`
  - `accounts`
  - `provider_profiles`
  - `consumer_profiles`

- `feat/provider-services`
  - `services`
  - `service_tags`
  - `service_endpoints`
  - `provider_upstreams`

- `feat/pricing-and-publish`
  - `pricing_models`

- `feat/revisions-and-change-tokens`
  - `service_revisions`

- `feat/quote-flow`
  - `quotes`

- `feat/invoke-core`
  - `invocations`

- `feat/x402-payment`
  - `payment_attempts`

- `feat/ledger-and-earnings`
  - `ledger_entries`

- `feat/payouts-reporting`
  - `payouts`

- `feat/moderation-admin`
  - `moderation_actions`

- `feat/service-health`
  - `service_health_checks`

## Phase 2 implementation notes

- `services.slug` is currently globally unique.
- `services.slug` uses lowercase slug-token format and must include at least one
  lowercase letter, so numeric-only slug values are invalid.
- `service_tags` are stored as lowercase slug tokens and replaced as a full set.
- `service_endpoints.key` is unique per service.
- `service_endpoints.key` uses the same lowercase slug-token format and must
  include at least one lowercase letter, so numeric-only key values are
  invalid.
- `provider_upstreams` are stored privately and are not exposed in API response models.
- `moderation_actions.service_id` is currently a scalar reference with no ORM relationship or DB foreign key to `services`.
- `moderation_actions.actor_account_id` is nullable and uses `ON DELETE SET NULL`.
- `service_health_checks.service_id` is currently a scalar reference with no ORM relationship or DB foreign key to `services`.
- `service_health_checks.status` is constrained to `pass`, `fail`, or `error`.

## Mutability policy

### Freely mutable while draft

Allowed:

- service metadata
- tags
- endpoint details
- schemas
- pricing
- upstream config
- timeout settings

Only while lifecycle is `DRAFT`.

The landed Phase 2 route surface supports create/list/get/update for draft services,
tag replacement, endpoint create/update, and upstream upsert. Delete routes are not
part of the current draft-management surface.

### Mutable after publish only with revision and change-token bump

Allowed but controlled:

- input schema
- output schema
- pricing
- access mode
- endpoint activation state
- timeout
- any contract-affecting field

Required behaviour:

- create a revision snapshot
- bump change token
- invalidate or reject stale quotes where required

### Mutable after publish without revision

Allowed:

- support details
- descriptive text
- examples
- non-contract metadata

### Immutable after first publish

Do not change:

- service primary identity
- slug
- stable endpoint key
- ownership of a service
- historical revisions
- quotes
- invocations
- payment attempts
- ledger entries

### Frozen while suspended

Block mutation of:

- upstream executable routing
- upstream credentials
- payout wallet
- schemas
- pricing
- access mode

Allow only minimal non-executable remediation metadata.

## Reasoning

Anything that changes what a consumer agent is buying must not change silently.
