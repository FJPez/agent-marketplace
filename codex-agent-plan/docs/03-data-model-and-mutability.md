# 03 Data Model and Mutability

## Table groups

### Identity

- `accounts`
- `api_keys`
- `wallet_change_log`

Identity notes:

- `accounts` owns wallet identity, auth nonce state, token invalidation state,
  account type, admin flag, and display name
- legacy `accounts` rows may keep a null `wallet_address` until the owner links
  a wallet through auth; authenticated actors always resolve with a linked
  wallet address
- `api_keys` stores hashed bearer keys with optional expiry, last-used
  tracking, and revocation state
- `wallet_change_log` is an append-only audit trail for wallet rotation
- `provider_profiles` and `consumer_profiles` are removed; provider and
  consumer behaviour now hangs off the unified account model

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

### Operations

- `moderation_actions`
- `service_health_checks`

## Table ownership by branch or workstream

- `feat/database-core`
  - original `accounts` baseline

- auth redesign follow-on
  - `feat/unified-profile`
    - unified `accounts` shape and removal of legacy profile tables
  - `feat/api-key-auth`
    - `api_keys`
  - `feat/wallet-change`
    - `wallet_change_log`

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

- `feat/moderation-admin`
  - `moderation_actions`

- `feat/service-health`
  - `service_health_checks`

## Current implementation notes

- `services.provider_account_id` references `accounts.id`
- `services.slug` is globally unique
- `services.slug` uses lowercase slug-token format and must include at least
  one lowercase letter, so numeric-only slug values are invalid
- `service_tags` are stored as lowercase slug tokens and replaced as a full set
- `service_endpoints.key` is unique per service
- `service_endpoints.key` uses the same lowercase slug-token format and must
  include at least one lowercase letter, so numeric-only key values are
  invalid
- `provider_upstreams` are stored privately and are not exposed in API response
  models
- `moderation_actions.service_id` is currently a scalar reference with no ORM
  relationship or DB foreign key to `services`
- `moderation_actions.actor_account_id` is nullable and uses `ON DELETE SET NULL`
- `service_health_checks.service_id` is currently a scalar reference with no
  ORM relationship or DB foreign key to `services`
- `service_health_checks.status` is constrained to `pass`, `fail`, or `error`
- any authenticated account can own provider services and invoke marketplace
  routes; there is no separate provider or consumer profile gate

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

The landed provider-management surface supports create/list/get/update for draft
services, tag replacement, endpoint create/update, and upstream upsert. Delete
routes are not part of the current draft-management surface.

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

### Account and auth mutability

- `display_name` is mutable through `PATCH /v1/account/me`
- `wallet_address` is mutable only through the wallet-change flow
- `token_version` is mutable only by auth and security flows that need to
  invalidate existing bearer tokens
- API keys can be created, listed, expired, and revoked, but plaintext key
  material is returned only at creation time

### Frozen while suspended

Block mutation of:

- upstream executable routing
- upstream credentials
- schemas
- pricing
- access mode

Allow only minimal non-executable remediation metadata.

## Reasoning

Anything that changes what a consumer agent is buying must not change silently.
Anything that changes who controls an account or which bearer credentials remain
valid must be auditable and explicit.
