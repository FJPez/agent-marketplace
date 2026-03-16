# Demo Setup

This demo gives you a local end-to-end path for:

- discovery
- quote creation
- free invoke
- paid `402 Payment Required`
- signed paid retry with the Python x402 client

The paid retry still depends on the external facilitator. In my local run on March 16, 2026, the retry reached the app correctly and then came back with `502 {"detail":"facilitator unavailable"}` from the live facilitator.

## Prerequisites

- Python 3.12
- `uv`
- PostgreSQL on `localhost:5432`
  - You can use your own local Postgres, or `docker compose up -d postgres`
- A Base Sepolia private key for the buyer wallet
  - Export it as `CONSUMER_PRIVATE_KEY`

## One-Time Setup

Install dependencies:

```bash
uv sync
```

Apply migrations:

```bash
uv run alembic upgrade head
```

## Start the Mock Upstream

In one terminal:

```bash
uv run python examples/mock_upstream.py
```

This starts a FastAPI app on `http://127.0.0.1:9000` and logs every incoming header so you can inspect the `X-Agent-Marketplace-*` signing headers.

## Seed the Demo Data

In a second terminal, seed the demo service so it points at the local mock upstream:

```bash
APP_X402_PAY_TO_ADDRESS=0x000000000000000000000000000000000000c0de \
APP_DEMO_UPSTREAM_BASE_URL=http://127.0.0.1:9000 \
APP_DEMO_FREE_UPSTREAM_PATH=/free-ping \
APP_DEMO_PAID_UPSTREAM_PATH=/paid-summary \
uv run python scripts/seed_demo.py
```

Expected output:

```text
provider_account_id=1
consumer_account_id=2
service_id=1
service_slug=demo-agent-service
free_endpoint_id=1
paid_endpoint_id=2
```

`consumer_account_id=2` is the default used by `examples/client.py`. If your output differs, export `CONSUMER_ACCOUNT_ID` before running the client.

## Start the Marketplace API

In a third terminal:

```bash
APP_X402_PAY_TO_ADDRESS=0x000000000000000000000000000000000000c0de \
APP_DEMO_UPSTREAM_BASE_URL=http://127.0.0.1:9000 \
APP_DEMO_FREE_UPSTREAM_PATH=/free-ping \
APP_DEMO_PAID_UPSTREAM_PATH=/paid-summary \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The API will be available at `http://127.0.0.1:8000`.

## Run the Example Client

In a fourth terminal:

```bash
export CONSUMER_PRIVATE_KEY=0xYOUR_BASE_SEPOLIA_PRIVATE_KEY
export CONSUMER_ACCOUNT_ID=2
export API_BASE_URL=http://127.0.0.1:8000

uv run python examples/client.py
```

The example client will:

1. Call `GET /v1/services`
2. Call `POST /v1/services/demo-agent-service/quote`
3. Call `POST /v1/invoke/demo-agent-service` for the free endpoint
4. Call the paid endpoint, receive `402 Payment Required`, generate a signed payment with `x402HTTPClient`, and retry the same request

## Expected Results

You should see:

- a discovered `demo-agent-service`
- a successful quote response
- a successful free invoke response
- an initial paid response with `402` and a `PAYMENT-REQUIRED` header

If the facilitator is available and your buyer wallet can be settled on Base Sepolia, the paid retry should return `200` and a `PAYMENT-RESPONSE` header.

If the facilitator is unavailable, you will see the retry fail with:

```text
502 {"detail":"facilitator unavailable"}
```

That means the local app, seeding, routing, challenge generation, and x402 client retry are working, but the external facilitator could not complete the payment leg.

## Troubleshooting

If `uv run pytest` fails on collection, the repo is configured to use `--import-mode=importlib` by default in `pyproject.toml`; just run `uv run pytest` normally.

If quote creation returns a validation error around `service_change_token`, rerun the demo seed command above. The seed now refreshes the seeded revision and change token on reruns.

If the paid retry fails before it reaches the app, confirm:

- `CONSUMER_PRIVATE_KEY` is set
- the app is returning `PAYMENT-REQUIRED`
- `examples/client.py` is using `http://127.0.0.1:8000`

If the app cannot reach the mock upstream, confirm:

- `examples/mock_upstream.py` is running on port `9000`
- the API was started with `APP_DEMO_UPSTREAM_BASE_URL=http://127.0.0.1:9000`
