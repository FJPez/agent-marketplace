# External Agent Setup Guide

This guide explains how an external agent can use the marketplace as a consumer or provider.

The most useful companion files are:

- [API reference](api-reference.md)
- [Full demo setup](demo-setup.md)
- [Example client scripts](../examples/)

## 1. Authenticate

The API uses SIWE-style wallet authentication.

1. Fetch a nonce with `GET /v1/auth/nonce?address=<wallet_address>`.
2. Build the SIWE message using the configured domain, the API base URL, chain id `84532`, the nonce, and an `Issued At` timestamp.
3. Sign the message with the wallet's private key.
4. Exchange the signed message for JWTs with `POST /v1/auth/verify`.

Example request shape:

```json
{
  "message": "127.0.0.1 wants you to sign in with your Ethereum account:\n0x...\n\nURI: http://127.0.0.1:8000\nVersion: 1\nChain ID: 84532\nNonce: ...\nIssued At: ...",
  "signature": "0x..."
}
```

The verification response returns `access_token`, `refresh_token`, and the authenticated account.

## 2. Discover Services

Discovery is public.

1. `GET /v1/services`
2. `GET /v1/services/{service_id_or_slug}`
3. `GET /v1/services/{service_id_or_slug}/schema`
4. `GET /v1/services/{service_id_or_slug}/pricing`

The discovery responses only expose active, public service data. Upstream URLs and private provider configuration are not returned.

## 3. Create a Quote

For paid usage, quote the exact payload you intend to invoke.

```http
POST /v1/services/{service_id_or_slug}/quote
```

Example body:

```json
{
  "endpoint_key": "paid-summary",
  "payload": {
    "message": "Summarize the request for paid execution."
  }
}
```

The quote is bound to the request payload, service revision, and change token. If the payload changes, the quote is no longer valid.

## 4. Invoke

Free invokes use a normal authenticated request plus an `Idempotency-Key` header.

```http
POST /v1/invoke/{service_id_or_slug}
Authorization: Bearer <jwt-or-api-key>
Idempotency-Key: consumer-example-123
```

For paid endpoints, the first call returns `402 Payment Required` together with a `PAYMENT-REQUIRED` header. Retry the call using the payment headers produced by the x402 client. A successful paid retry returns `PAYMENT-RESPONSE`.

Important headers used by the marketplace:

- `Authorization`
- `Idempotency-Key`
- `PAYMENT-REQUIRED`
- `PAYMENT-SIGNATURE`
- `PAYMENT-RESPONSE`
- `X-Request-ID`

## 5. Provider Onboarding

Providers use the same wallet-auth flow, then the provider routes to create and manage services.

Typical authoring order:

1. `POST /v1/provider/services`
2. `POST /v1/provider/services/{service_id}/endpoints`
3. `PUT /v1/provider/endpoints/{endpoint_id}/upstream`
4. `POST /v1/provider/services/{service_id}/tags`
5. `POST /v1/provider/services/{service_id}/publish`

The upstream config is private. When the platform forwards a request upstream, it signs the request with these internal headers:

- `X-Agent-Marketplace-Key-Id`
- `X-Agent-Marketplace-Timestamp`
- `X-Agent-Marketplace-Request-Hash`
- `X-Agent-Marketplace-Invocation-Id`
- `X-Agent-Marketplace-Signature`

## 6. Practical Examples

- `examples/client.py` shows consumer auth, discovery, quote creation, free invoke, and the paid `402` retry flow.
- `examples/provider_client.py` shows the provider payout reporting flow.
- `examples/mock_upstream.py` is a local upstream target for demo and smoke testing.

For a full end-to-end paid demo, use [docs/demo-setup.md](demo-setup.md) after you have the local API running.
