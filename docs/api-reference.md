# API Reference

This document is the submission-facing API reference for the agent marketplace backend.

The implementation is HTTP-first, versioned under `/v1` for domain routes, and uses FastAPI-style JSON bodies and status codes.

## Common Mechanics

### Base URLs

- Health routes: `/health`, `/health/live`, `/health/ready`
- Domain routes: `/v1/...`

### Common Headers

- `Authorization: Bearer <jwt-or-api-key>` for protected routes that accept generic bearer auth
- `Idempotency-Key` for invoke requests and provider payout requests
- `X-Request-ID` for request correlation
- `PAYMENT-REQUIRED` on `402 Payment Required` responses
- `PAYMENT-SIGNATURE` on paid retry requests produced by the x402 client
- `PAYMENT-RESPONSE` on successful paid invoke responses
- The service uses `X-Request-ID` for request correlation and echoes it when present

### Common Error Shape

Most route handlers raise FastAPI HTTP errors with JSON bodies shaped like:

```json
{
  "detail": "human-readable message"
}
```

## Authentication Matrix

- Public: health routes, discovery routes, quote creation, and auth nonce / verify / refresh
- Generic bearer auth: provider authoring, invoke, invocation reads, provider earnings, ledger, public payout reads, and admin routes
- JWT-only: API-key lifecycle, account self-service, wallet rotation, and provider payout execution

## Health

### `GET /health`

- Auth: none
- Request: no body, no parameters
- Success: `200` with `{"status":"ok"}`
- Errors: none expected in the normal path

### `GET /health/live`

- Auth: none
- Request: no body, no parameters
- Success: `200` with `{"status":"ok"}`
- Errors: none expected in the normal path

### `GET /health/ready`

- Auth: none
- Request: no body, no parameters
- Success: `200` with `{"status":"ok"}`
- Errors: `503` with `{"detail":"..."}` when readiness checks fail

## Auth

### `GET /v1/auth/nonce`

- Auth: none
- Request: query parameter `address` with a 42-character wallet address
- Success: `200` with `{"nonce":"..."}`
- Errors: `422` if the address is malformed

Example response:

```json
{
  "nonce": "f1d0b88e3f8b4dce"
}
```

### `POST /v1/auth/verify`

- Auth: none
- Request: body with `message` and `signature`
- Success: `200` with `access_token`, `refresh_token`, and the authenticated account
- Errors: `401` when the signature or message is invalid

Example request:

```json
{
  "message": "127.0.0.1 wants you to sign in with your Ethereum account:\n0x...\n\nURI: http://127.0.0.1:8000\nVersion: 1\nChain ID: 84532\nNonce: f1d0b88e3f8b4dce\nIssued At: 2026-03-17T12:00:00Z",
  "signature": "0x..."
}
```

Example response:

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "account": {
    "id": 1,
    "wallet_address": "0x...",
    "account_type": "human",
    "is_admin": false,
    "display_name": "Demo Account",
    "created_at": "2026-03-17T12:00:00Z",
    "updated_at": "2026-03-17T12:00:00Z"
  }
}
```

### `POST /v1/auth/refresh`

- Auth: none
- Request: body with `refresh_token`
- Success: `200` with a fresh `access_token`
- Errors: `401` when the refresh token is invalid or expired

### `POST /v1/auth/api-keys`

- Auth: JWT-only
- Request: body with optional `name` and `expires_at`
- Success: `201` with metadata plus the plaintext `api_key` once
- Errors: `422` when the request is invalid

### `GET /v1/auth/api-keys`

- Auth: JWT-only
- Request: no body
- Success: `200` with a list of API-key metadata
- Errors: none expected in the normal path

### `DELETE /v1/auth/api-keys/{api_key_id}`

- Auth: JWT-only
- Request: path parameter `api_key_id`
- Success: `204` with no body
- Errors: `404` when the key does not exist

## Account

### `GET /v1/account/me`

- Auth: JWT-only
- Request: no body
- Success: `200` with the current account
- Errors: `404` when the account cannot be found

### `PATCH /v1/account/me`

- Auth: JWT-only
- Request: body with optional `display_name`
- Success: `200` with the updated account
- Errors: `422` when no updatable field is provided or the data is invalid

Example request:

```json
{
  "display_name": "Demo Provider"
}
```

### `POST /v1/account/wallet`

- Auth: JWT-only
- Request: body with `wallet_address`
- Success: `200` with a wallet-change nonce and expiry
- Errors: `409` when the requested change conflicts with account state, `422` for invalid input

### `POST /v1/account/wallet/confirm`

- Auth: JWT-only
- Request: body with `message` and `signature`
- Success: `200` with fresh tokens and the updated account
- Errors: `409` when the wallet change flow is not valid or has expired

## Provider Authoring

### `POST /v1/provider/services`

- Auth: generic bearer
- Request: body with `slug`, `name`, `summary`, and optional `description`
- Success: `201` with the created service
- Errors: `409` on conflicts, `422` for invalid input

### `GET /v1/provider/services`

- Auth: generic bearer
- Request: no body
- Success: `200` with the authenticated account's services
- Errors: none expected in the normal path

### `GET /v1/provider/services/{service_id}`

- Auth: generic bearer
- Request: path parameter `service_id`
- Success: `200` with the owned service detail
- Errors: `404` when the service does not exist or is not owned

### `PATCH /v1/provider/services/{service_id}`

- Auth: generic bearer
- Request: body with optional `name`, `summary`, and `description`
- Success: `200` with the updated service
- Errors: `409` on invalid state transitions, `422` for invalid data

### `POST /v1/provider/services/{service_id}/tags`

- Auth: generic bearer
- Request: body with `tags` as the full replacement list
- Success: `200` with the service and updated tags
- Errors: `404` if the service is not owned, `422` for invalid input

### `POST /v1/provider/services/{service_id}/publish`

- Auth: generic bearer
- Request: no body
- Success: `200` with the published service
- Errors: `409` when publish requirements are not met

### `POST /v1/provider/services/{service_id}/endpoints`

- Auth: generic bearer
- Request: body with endpoint `key`, `name`, `summary`, `description`, `access_mode`, `request_schema`, `response_schema`, `timeout_seconds`, `is_enabled`, and optional `pricing`
- Success: `201` with the created endpoint
- Errors: `409` on invalid service state, `422` for schema or pricing problems

### `PATCH /v1/provider/endpoints/{endpoint_id}`

- Auth: generic bearer
- Request: body with optional endpoint fields and optional `pricing`
- Success: `200` with the updated endpoint
- Errors: `404` if the endpoint is not owned, `409` on invalid state transitions, `422` for invalid input

### `PUT /v1/provider/endpoints/{endpoint_id}/upstream`

- Auth: generic bearer
- Request: body with `base_url`, `path`, `http_method`, and optional `config`
- Success: `204` with no body
- Errors: `404` if the endpoint is not owned, `409` on invalid state transitions, `422` for invalid input

Example upstream config payload:

```json
{
  "base_url": "https://provider.example.com",
  "path": "/invoke",
  "http_method": "POST",
  "config": {
    "auth": {
      "type": "hmac_sha256",
      "key_id": "gateway-key",
      "secret": "gateway-secret"
    }
  }
}
```

## Discovery

### `GET /v1/services`

- Auth: none
- Request: no body
- Success: `200` with the public service list
- Errors: none expected in the normal path

### `GET /v1/services/{service_id_or_slug}`

- Auth: none
- Request: path parameter `service_id_or_slug`
- Success: `200` with the public service detail
- Errors: `404` when the service does not exist or is not publicly available
- Lookup rule: a path segment made only of digits is treated as a service id, not a slug

### `GET /v1/services/{service_id_or_slug}/schema`

- Auth: none
- Request: path parameter `service_id_or_slug`
- Success: `200` with the enabled endpoint schemas
- Errors: `404` when the service does not exist or is not publicly available

### `GET /v1/services/{service_id_or_slug}/pricing`

- Auth: none
- Request: path parameter `service_id_or_slug`
- Success: `200` with the enabled endpoint pricing data
- Errors: `404` when the service does not exist or is not publicly available

### `POST /v1/services/{service_id_or_slug}/quote`

- Auth: none
- Request: body with `endpoint_key` and `payload`
- Success: `201` with a quote bound to the request payload
- Errors: `404` when the service or endpoint cannot be found, `409` when the quote cannot be created for the current state

Example request:

```json
{
  "endpoint_key": "paid-summary",
  "payload": {
    "message": "Summarize this marketplace request."
  }
}
```

Example response:

```json
{
  "id": 1,
  "service_id": 1,
  "endpoint_key": "paid-summary",
  "pricing_type": "fixed_per_call",
  "amount_minor": 100,
  "currency": "USDC",
  "request_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "service_revision_id": 1,
  "service_change_token": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "expires_at": "2026-03-17T12:05:00Z",
  "created_at": "2026-03-17T12:00:00Z"
}
```

## Invoke

### `POST /v1/invoke/{service_id_or_slug}`

- Auth: generic bearer
- Request: body with `endpoint_key`, `payload`, and optional `quote_id`
- Required header: `Idempotency-Key`
- Success: `200` with the invocation record, or `402` for unpaid paid-endpoint requests
- Errors: `404` for missing services or invocations, `409` for conflicts or in-progress requests, `502` for upstream failures, `504` for upstream timeouts

Example free-invoke request:

```json
{
  "endpoint_key": "free-ping",
  "payload": {
    "message": "hello from the free route"
  }
}
```

Example invocation response:

```json
{
  "id": 1,
  "service_id": 1,
  "endpoint_key": "free-ping",
  "access_mode": "free",
  "quote_id": null,
  "idempotency_key": "consumer-example-1",
  "request_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "status": "succeeded",
  "upstream_status_code": 200,
  "response_payload": {
    "message": "pong",
    "mode": "free"
  },
  "error_message": null,
  "created_at": "2026-03-17T12:00:00Z"
}
```

### `GET /v1/invocations/{invocation_id}`

- Auth: generic bearer
- Request: path parameter `invocation_id`
- Success: `200` with the invocation detail
- Errors: `404` when the invocation is not accessible

### `GET /v1/invocations`

- Auth: generic bearer
- Request: no body
- Success: `200` with the caller's invocation list
- Errors: none expected in the normal path

## Finance

### `GET /v1/provider/earnings`

- Auth: generic bearer
- Request: no body
- Success: `200` with earnings totals grouped by currency
- Errors: none expected in the normal path

### `GET /v1/provider/ledger`

- Auth: generic bearer
- Request: no body
- Success: `200` with ledger entries
- Errors: none expected in the normal path

### `GET /v1/provider/payouts`

- Auth: generic bearer
- Request: optional `status` query parameter
- Success: `200` with payout summaries and payout records
- Errors: none expected in the normal path

### `POST /v1/provider/payouts`

- Auth: JWT-only
- Request: no body, but `Idempotency-Key` is required
- Success: `200` with payout-request results
- Errors: `409` when a duplicate or conflicting request is submitted

Example response:

```json
{
  "idempotency_key": "example-provider-payout-1",
  "requested_count": 2,
  "sent_count": 2,
  "failed_count": 0,
  "payouts": []
}
```

## Admin

### `POST /v1/admin/services/{service_id}/suspend`

- Auth: admin actor
- Request: body with `reason`
- Success: `201` with the moderation action
- Errors: `404` when the service cannot be found, `409` for invalid transitions

### `POST /v1/admin/services/{service_id}/restore`

- Auth: admin actor
- Request: body with `reason`
- Success: `201` with the moderation action
- Errors: `404` when the service cannot be found, `409` for invalid transitions

### `POST /v1/admin/services/{service_id}/delist`

- Auth: admin actor
- Request: body with `reason`
- Success: `201` with the moderation action
- Errors: `404` when the service cannot be found, `409` for invalid transitions

### `GET /v1/admin/moderation/actions`

- Auth: admin actor
- Request: `service_id` query parameter
- Success: `200` with the moderation action history
- Errors: `422` for invalid query values

## Notes for Examiners

- Quote creation is public in the codebase. Consumers do not need to authenticate to ask for a quote.
- Discovery routes only expose public, active service data.
- Provider upstream URLs and upstream credentials are never returned by public discovery responses.
- The paid invoke flow is a two-step `402` challenge/settle flow backed by x402-compatible headers.
- The provider payout request path is separate from payout reporting and requires JWT auth plus idempotency protection.
