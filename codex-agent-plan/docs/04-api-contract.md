# 04 API Contract

## Identity routes

- `POST /v1/providers`
- `GET /v1/providers/me`
- `PATCH /v1/providers/me`
- `POST /v1/consumers`

### Identity route behaviour

- `POST /v1/providers`
  - without `X-Account-Id`: bootstrap a new account and create a provider profile
  - with valid `X-Account-Id`: create a provider profile for the authenticated account
- `GET /v1/providers/me`
  - requires `X-Account-Id`
- `PATCH /v1/providers/me`
  - requires `X-Account-Id`
- `POST /v1/consumers`
  - without `X-Account-Id`: bootstrap a new account and create a consumer profile
  - with valid `X-Account-Id`: create a consumer profile for the authenticated account
- identity profile responses include `account_id`, `display_name`, and `created_at`

## Provider management routes

- `POST /v1/provider/services`
- `PATCH /v1/provider/services/{service_id}`
- `GET /v1/provider/services`
- `GET /v1/provider/services/{service_id}`
- `POST /v1/provider/services/{service_id}/tags`
- `POST /v1/provider/services/{service_id}/endpoints`
- `PATCH /v1/provider/endpoints/{endpoint_id}`
- `PUT /v1/provider/endpoints/{endpoint_id}/upstream`

## Publish and pricing routes

- `POST /v1/provider/services/{service_id}/publish`
- optional unpublish route if supported

## Discovery routes

- `GET /v1/services`
- `GET /v1/services/{service_id_or_slug}`
- `GET /v1/services/{service_id_or_slug}/schema`
- `GET /v1/services/{service_id_or_slug}/pricing`
- optional `GET /v1/services/{service_id_or_slug}/health`

## Quote and invoke routes

- `POST /v1/services/{service_id_or_slug}/quote`
- `POST /v1/invoke/{service_id_or_slug}`
- `GET /v1/invocations/{invocation_id}`
- `GET /v1/invocations`

## Finance routes

- `GET /v1/provider/earnings`
- `GET /v1/provider/ledger`
- `GET /v1/provider/payouts`

## Admin routes

- `POST /v1/admin/services/{service_id}/suspend`
- `POST /v1/admin/services/{service_id}/restore`
- `POST /v1/admin/services/{service_id}/delist`
- `GET /v1/admin/moderation/actions`

## Behavioural rules

### Quote

- Quote must bind to request hash.
- Quote must bind to the current contract revision and change token.
- Expired quotes must be rejected.

### Free invoke

- Validate request.
- Check moderation and lifecycle rules.
- Forward to provider.
- Record invocation.

### Paid invoke

- Validate request.
- Validate quote and request hash.
- Validate revision and change token.
- If no valid payment data, return `402 Payment Required`.
- If payment is valid and safely settled, forward to provider.
- Record invocation and financial outcome.

## Contract rules

- Public APIs must not leak upstream base URLs or upstream credentials.
- Response models must be explicit.
- Error responses should follow a consistent project-wide format.
- protected identity routes use `X-Account-Id` as the current actor header
- request correlation uses `X-Request-ID`
- responses echo `X-Request-ID` on success and unhandled `500` responses
