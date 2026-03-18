# Example Flows

The files in this directory are runnable examples for local demos and
integration testing. They are intentionally small and focused so you can try
specific flows without stepping through the whole application.

## Scripts

- `mock_upstream.py`
  - Starts a local FastAPI upstream on `http://127.0.0.1:9000`
  - Logs all incoming headers so you can inspect marketplace signing headers
  - Exposes `/free-ping` and `/paid-summary`
- `provider_publish.py`
  - Authenticates as a provider
  - Creates or updates the demo service
  - Creates or updates the free and paid endpoints
  - Points both endpoints at the mock upstream
  - Publishes the service
- `minimal_consumer.py`
  - Authenticates as a consumer
  - Lists public services
  - Loads public detail, schema, and pricing
  - Creates a quote for the paid endpoint
  - Runs a free invoke with idempotency headers
- `client.py`
  - Full consumer demo
  - Creates a quote
  - Runs a free invoke
  - Runs the paid invoke flow with x402 settlement
- `provider_client.py`
  - Provider payout demo
  - Lists payouts and requests payout execution

## Recommended Order

1. Start the mock upstream:

```bash
uv run python examples/mock_upstream.py
```

2. Start the marketplace API in a second terminal:

```bash
make demo-api
```

3. Publish or refresh the demo service:

```bash
uv run python examples/provider_publish.py
```

4. Run the lightweight consumer example:

```bash
uv run python examples/minimal_consumer.py
```

5. Run the full paid demo if you have the Base Sepolia and CDP prerequisites:

```bash
uv run python examples/client.py
uv run python examples/provider_client.py
```

## Required Environment

The examples assume these variables are available:

- `API_BASE_URL`
  - Marketplace API base URL
  - Defaults to `http://127.0.0.1:8000`
- `SIWE_DOMAIN`
  - SIWE domain used when signing in
  - Defaults to `127.0.0.1`
- `CONSUMER_PRIVATE_KEY`
  - Required for the consumer examples
- `PROVIDER_PRIVATE_KEY`
  - Required for the provider examples
- `SERVICE_SLUG`
  - Optional service slug override
  - Defaults to `demo-agent-service`
- `APP_DEMO_UPSTREAM_BASE_URL`
  - Optional upstream base URL override for `provider_publish.py`
  - Defaults to `http://127.0.0.1:9000`
- `APP_DEMO_FREE_UPSTREAM_PATH`
  - Optional free endpoint path override
  - Defaults to `/free-ping`
- `APP_DEMO_PAID_UPSTREAM_PATH`
  - Optional paid endpoint path override
  - Defaults to `/paid-summary`

The full paid demo also needs the x402 and Base Sepolia variables described in
[`docs/demo-setup.md`](../docs/demo-setup.md).

## Demo Modes

- Local-safe mode
  - `mock_upstream.py`
  - `provider_publish.py`
  - `minimal_consumer.py`
  - This mode does not require CDP facilitator credentials or payout setup
- Full paid mode
  - `client.py`
  - `provider_client.py`
  - This mode exercises x402 settlement and provider payouts
