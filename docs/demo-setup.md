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
- a Base Sepolia private key for the buyer wallet
- a Base Sepolia address for the provider payout wallet

## What You Need Before Starting

You need two wallet roles for the full paid demo:

- Buyer wallet:
  - used by `examples/client.py`
  - must be an EVM wallet on Base Sepolia
  - you must have the private key locally as `CONSUMER_PRIVATE_KEY`
- Provider payout wallet:
  - set as `APP_X402_PAY_TO_ADDRESS`
  - only the address is needed by the local API
  - this is the wallet the payment requirement points to

For the full paid retry path, the buyer wallet should be prepared for Base Sepolia:

- add Base Sepolia in your wallet app if it is not already present
- fund the buyer wallet with Base Sepolia ETH for gas
- make sure the buyer wallet can hold Base Sepolia USDC
- never commit the private key to the repo or put it in `.env.example`

If you only want to test discovery, quoting, free invoke, and the initial `402 Payment Required` response, you do not need a fully funded buyer wallet.

## One-Time Setup

Install dependencies:

```bash
uv sync
```

Create your local `.env`:

```bash
cp .env.example .env
```

Then edit `.env` so it looks like this for the local demo:

```dotenv
APP_ENV=dev
APP_TITLE=Agent Marketplace Backend
APP_DEBUG=false
APP_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agent_marketplace
APP_QUOTE_TTL_SECONDS=300
APP_X402_FACILITATOR_URL=https://x402.org/facilitator
APP_X402_NETWORK=base-sepolia
APP_X402_NETWORK_CAIP2=eip155:84532
APP_X402_PAY_TO_ADDRESS=0xYOUR_BASE_SEPOLIA_PROVIDER_ADDRESS
APP_DEMO_UPSTREAM_BASE_URL=http://127.0.0.1:9000
APP_DEMO_FREE_UPSTREAM_PATH=/free-ping
APP_DEMO_PAID_UPSTREAM_PATH=/paid-summary
```

What each setting does:

- `APP_ENV=dev`
  - local development mode
- `APP_TITLE=Agent Marketplace Backend`
  - FastAPI app title
- `APP_DEBUG=false`
  - keep this `false` unless you explicitly want debug mode
- `APP_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agent_marketplace`
  - local Postgres connection string
  - change only if your Postgres host, port, user, password, or db name differ
- `APP_QUOTE_TTL_SECONDS=300`
  - quotes expire after 5 minutes
- `APP_X402_FACILITATOR_URL=https://x402.org/facilitator`
  - the repo default and the common public facilitator URL used in this demo
  - if you operate your own facilitator, replace this value
- `APP_X402_NETWORK=base-sepolia`
  - human-readable network name used by the app
- `APP_X402_NETWORK_CAIP2=eip155:84532`
  - CAIP-2 network identifier for Base Sepolia
- `APP_X402_PAY_TO_ADDRESS=0xYOUR_BASE_SEPOLIA_PROVIDER_ADDRESS`
  - provider payout address advertised in the x402 payment requirement
  - must be a Base Sepolia EVM address
- `APP_DEMO_UPSTREAM_BASE_URL=http://127.0.0.1:9000`
  - points the seeded demo service at the local mock upstream
- `APP_DEMO_FREE_UPSTREAM_PATH=/free-ping`
  - free endpoint path on the mock upstream
- `APP_DEMO_PAID_UPSTREAM_PATH=/paid-summary`
  - paid endpoint path on the mock upstream

Start Postgres if you need it:

```bash
docker compose up -d postgres
```

If port `5432` is already taken on your machine, use your existing local Postgres instead of the compose service.

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

In a second terminal, seed the demo service:

```bash
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

What these values mean:

- `CONSUMER_PRIVATE_KEY`
  - buyer wallet private key for Base Sepolia
  - used only by the example client
- `CONSUMER_ACCOUNT_ID`
  - marketplace consumer account id from the seed output
- `API_BASE_URL`
  - local marketplace API base URL

The example client uses the installed Python x402 client and Base Sepolia network `eip155:84532`.

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

## Common Defaults

These are the values used by this repo unless you override them:

- facilitator URL: `https://x402.org/facilitator`
- network name: `base-sepolia`
- network CAIP-2 id: `eip155:84532`
- local API URL: `http://127.0.0.1:8000`
- local mock upstream URL: `http://127.0.0.1:9000`
- seeded service slug: `demo-agent-service`
- seeded consumer account id: usually `2` on a clean local database

## Troubleshooting

If `uv run pytest` fails on collection, the repo is configured to use `--import-mode=importlib` by default in `pyproject.toml`; just run `uv run pytest` normally.

If quote creation returns a validation error around `service_change_token`, rerun the demo seed command above. The seed now refreshes the seeded revision and change token on reruns.

If the paid retry fails before it reaches the app, confirm:

- `CONSUMER_PRIVATE_KEY` is set
- the app is returning `PAYMENT-REQUIRED`
- `examples/client.py` is using `http://127.0.0.1:8000`
- the buyer wallet is configured for Base Sepolia
- the buyer wallet has any required testnet funds for the path you are attempting

If the app cannot reach the mock upstream, confirm:

- `examples/mock_upstream.py` is running on port `9000`
- the API was started with `APP_DEMO_UPSTREAM_BASE_URL=http://127.0.0.1:9000`

If the paid retry returns `502 {"detail":"facilitator unavailable"}`, the local demo still proved:

- the quote path works
- the free invoke path works
- the paid challenge is generated correctly
- the example x402 client signed and retried the paid request

That specific error means the external facilitator did not complete verify/settle for the retry request.
