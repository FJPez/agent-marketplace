# Example Flows

The files in this directory are runnable manual demo and integration-reference
scripts. They show the highest-value user journeys, but they are not intended to
mirror every route family in the API.

## Scripts

- `mock_upstream.py`
  - starts a local FastAPI upstream on `http://127.0.0.1:9000`
  - logs incoming marketplace signing headers
  - exposes `/free-ping` and `/paid-summary`
- `provider_publish.py`
  - authenticates as a provider
  - creates or updates the demo service and its endpoints through the public API
  - points the endpoints at the mock upstream
  - publishes the service
- `minimal_consumer.py`
  - authenticates as a consumer
  - lists public services
  - loads public detail, schema, and pricing
  - creates a quote for the paid endpoint
  - runs a free invoke with idempotency headers
- `api_key_client.py`
  - authenticates with a wallet-backed JWT
  - creates, lists, and revokes an API key
  - uses the API key on a generic bearer route
  - shows a JWT-only route rejecting the API key
- `client.py`
  - full consumer paid demo
  - creates a quote
  - runs a free invoke
  - runs the paid invoke flow with x402 settlement
- `provider_client.py`
  - provider finance demo
  - reads earnings and ledger data
  - lists payouts and requests payout execution

## Recommended Order

1. Start the mock upstream:

```bash
uv run python examples/mock_upstream.py
```

2. Start the marketplace API in a second terminal:

```bash
make demo-api
```

3. Choose one provider setup mode:

- Manual provider-authoring mode:

```bash
uv run python examples/provider_publish.py
```

- Deterministic seeded paid-demo mode:
  - use `make seed` from [`docs/demo-setup.md`](../docs/demo-setup.md)

4. Run the lightweight consumer example:

```bash
uv run python examples/minimal_consumer.py
```

5. Run the API-key example:

```bash
uv run python examples/api_key_client.py
```

6. Run the full paid demo if you have the Base Sepolia and CDP prerequisites:

```bash
uv run python examples/client.py
uv run python examples/provider_client.py
```

## Required Environment

The examples assume these variables are available:

- `API_BASE_URL`
  - marketplace API base URL
  - defaults to `http://127.0.0.1:8000`
- `SIWE_DOMAIN`
  - SIWE domain used when signing in
  - defaults to `127.0.0.1`
- `CONSUMER_PRIVATE_KEY`
  - required for `minimal_consumer.py`, `api_key_client.py`, and `client.py`
- `PROVIDER_PRIVATE_KEY`
  - required for `provider_publish.py` and `provider_client.py`
- `SERVICE_SLUG`
  - optional service slug override
  - defaults to `demo-agent-service`
- `APP_DEMO_UPSTREAM_BASE_URL`
  - optional upstream base URL override for `provider_publish.py`
  - defaults to `http://127.0.0.1:9000`
- `APP_DEMO_FREE_UPSTREAM_PATH`
  - optional free endpoint path override
  - defaults to `/free-ping`
- `APP_DEMO_PAID_UPSTREAM_PATH`
  - optional paid endpoint path override
  - defaults to `/paid-summary`

The full paid demo also needs the x402 and Base Sepolia variables described in
[`docs/demo-setup.md`](../docs/demo-setup.md).

## Covered And Not Covered

Covered by the runnable examples:

- wallet auth
- API-key lifecycle and bearer usage
- discovery, quote, free invoke, and paid invoke
- provider authoring and publish
- provider finance reads and payout request flow

Not covered by dedicated runnable scripts in this directory:

- wallet rotation
- admin moderation
- full invocation polling and lookup workflows

Those flows are still documented, but they are not part of the default runnable
demo set.
