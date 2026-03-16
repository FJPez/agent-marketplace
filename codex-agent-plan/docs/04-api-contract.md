# 04 API Contract

## Auth routes

- `GET /v1/auth/nonce`
- `POST /v1/auth/verify`
- `POST /v1/auth/refresh`
- `POST /v1/auth/api-keys`
- `GET /v1/auth/api-keys`
- `DELETE /v1/auth/api-keys/{api_key_id}`

### Auth route behaviour

- `GET /v1/auth/nonce`
  - requires a wallet `address` query parameter
  - normalizes the wallet address and returns a single-use nonce
- `POST /v1/auth/verify`
  - accepts a SIWE-style `message` and `signature`
  - returns `access_token`, `refresh_token`, and the authenticated account
- `POST /v1/auth/refresh`
  - accepts `refresh_token`
  - returns a fresh access token
- API-key routes
  - require bearer JWT auth
  - reject API-key bearer tokens with `403`
  - create returns plaintext key material exactly once
  - list returns metadata only
  - delete revokes an existing key and returns `204`

## Account routes

- `GET /v1/account/me`
- `PATCH /v1/account/me`
- `POST /v1/account/wallet`
- `POST /v1/account/wallet/confirm`

### Account route behaviour

- account routes require `Authorization: Bearer <jwt>`
- account routes reject API-key bearer tokens with `403`
- `GET /v1/account/me`
  - returns the authenticated account
- `PATCH /v1/account/me`
  - currently supports display-name updates
  - returns `422` if no updatable field is provided
- `POST /v1/account/wallet`
  - starts a wallet-change challenge
  - returns a nonce and expiry
- `POST /v1/account/wallet/confirm`
  - verifies the signed wallet-change message
  - rotates the wallet, invalidates prior JWTs, and returns fresh tokens plus
    the updated account

## Provider management routes

- `POST /v1/provider/services`
- `PATCH /v1/provider/services/{service_id}`
- `GET /v1/provider/services`
- `GET /v1/provider/services/{service_id}`
- `POST /v1/provider/services/{service_id}/tags`
- `POST /v1/provider/services/{service_id}/publish`
- `POST /v1/provider/services/{service_id}/endpoints`
- `PATCH /v1/provider/endpoints/{endpoint_id}`
- `PUT /v1/provider/endpoints/{endpoint_id}/upstream`

### Provider management route behaviour

- provider management routes require bearer auth and an authenticated account
- provider management routes do not require a separate provider profile
- `POST /v1/provider/services`
  - creates an owned draft service
- `GET /v1/provider/services`
  - lists owned services newest first
- `GET /v1/provider/services/{service_id}`
  - returns owned service detail including tags and endpoints
- `PATCH /v1/provider/services/{service_id}`
  - updates owned service metadata
  - draft services allow broad mutation
  - active services allow non-material updates without revision and material
    updates through revision and change-token handling
- `POST /v1/provider/services/{service_id}/tags`
  - replaces the full tag set for an owned service
- `POST /v1/provider/services/{service_id}/endpoints`
  - creates an owned endpoint
- `PATCH /v1/provider/endpoints/{endpoint_id}`
  - updates an owned endpoint
- `PUT /v1/provider/endpoints/{endpoint_id}/upstream`
  - upserts hidden upstream config and returns `204`
- explicit `null`
  - clears nullable fields such as service `description` and endpoint `summary`
    or `description`
  - returns `422` for non-nullable fields such as `name`
- provider responses expose `has_upstream` only and must not expose upstream
  payload fields
- suspended services block contract-affecting mutations and publish

## Publish and pricing routes

- `POST /v1/provider/services/{service_id}/publish`

### Publish and pricing behaviour

- endpoints support free and fixed-per-call pricing
- publish requires an owned service with at least one publishable endpoint
- paid endpoints require pricing before publish succeeds
- publish creates or refreshes the current revision and change token
- publish transitions the service to `ACTIVE`
- unpublish is not part of the current route surface

## Discovery routes

- `GET /v1/services`
- `GET /v1/services/{service_id_or_slug}`
- `GET /v1/services/{service_id_or_slug}/schema`
- `GET /v1/services/{service_id_or_slug}/pricing`

For discovery routes using `{service_id_or_slug}`, an all-digit path segment is
resolved as `service_id` only and does not fall back to slug matching.

### Discovery route behaviour

- discovery returns only public, active, non-delisted, non-suspended services
- endpoints must be enabled to appear in public detail and schema responses
- pricing and schema responses do not expose upstream config

## Quote and invoke routes

- `POST /v1/services/{service_id_or_slug}/quote`
- `POST /v1/invoke/{service_id_or_slug}`
- `GET /v1/invocations/{invocation_id}`
- `GET /v1/invocations`

## Finance routes

- `GET /v1/provider/earnings`
- `GET /v1/provider/ledger`

Finance routes require bearer auth and are scoped to the authenticated account.
Payout reporting is not part of the current route surface.

## Admin routes

- `POST /v1/admin/services/{service_id}/suspend`
- `POST /v1/admin/services/{service_id}/restore`
- `POST /v1/admin/services/{service_id}/delist`
- `GET /v1/admin/moderation/actions`

Admin routes require authenticated admin access and record moderation actions
that drive service availability.

## Service health routes

No public service-health route surface is implemented beyond `GET /health`.
`service_health_checks` currently acts as internal persistence and
publish-readiness scaffolding.

## Behavioural rules

### Quote

- Quote must bind to request hash
- Quote must bind to the current contract revision and change token
- Expired or stale quotes must be rejected

### Free invoke

- validate request
- check moderation and lifecycle rules
- forward to provider
- record invocation
- replay idempotent requests without a second upstream call

### Paid invoke

- validate request
- validate quote and request hash
- validate revision and change token
- if no valid payment data, return `402 Payment Required`
- if payment is valid and safely settled, forward to provider
- record invocation, payment attempt, and financial outcome

## Contract rules

- public APIs must not leak upstream base URLs or upstream credentials
- response models must be explicit
- protected routes use `Authorization: Bearer ...`
- protected routes may accept either JWTs or API keys unless the route
  specifically requires JWT-bound wallet/account state
- `X-Account-Id` is not part of the API contract
- any authenticated account can provide and consume marketplace services
- request correlation uses `X-Request-ID`
- responses echo `X-Request-ID` on success and unhandled `500` responses
