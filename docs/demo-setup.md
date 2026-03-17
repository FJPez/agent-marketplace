# Demo Setup

This demo gives you a local end-to-end path for:

- discovery
- quote creation
- free invoke
- paid `402 Payment Required`
- signed paid retry with the Python x402 client
- payment settlement through the CDP facilitator
- Base Sepolia transaction verification for the buyer and settlement wallet

## Prerequisites

- Python 3.12
- `uv`
- PostgreSQL on `localhost:5432`
  - You can use your own local Postgres, or `docker compose up -d postgres`
- a Base Sepolia private key for the buyer wallet
- a Base Sepolia address for the marketplace settlement wallet
- a Base Sepolia address for the provider payout wallet
- a CDP secret API key id and secret for the facilitator

## What You Need Before Starting

You need three wallet roles for the full paid demo:

- Buyer wallet:
  - used by `examples/client.py`
  - must be an EVM wallet on Base Sepolia
  - you must have the private key locally as `CONSUMER_PRIVATE_KEY`
- Marketplace settlement wallet:
  - set as `APP_X402_PAY_TO_ADDRESS`
  - only the address is needed for paid invoke settlement
  - this is the wallet the x402 payment requirement points to
- Provider payout wallet:
  - stored on the provider account profile
  - receives funds only when the provider later requests payout

For the full paid retry path, the buyer wallet should be prepared for Base Sepolia:

- add Base Sepolia in your wallet app if it is not already present
- fund the buyer wallet with Base Sepolia ETH for gas
- fund the buyer wallet with Base Sepolia USDC
- never commit the private key to the repo or put it in `.env.example`

The provider payout wallet should also be a Base Sepolia EVM address. It does
not need a private key in this repo; only the address is required so the payout
request flow knows where to send provider funds.

You also need CDP facilitator credentials:

- `APP_X402_CDP_API_KEY_ID`
  - your CDP secret API key id
  - usually shaped like `organizations/<org-id>/apiKeys/<key-id>`
- `APP_X402_CDP_API_KEY_SECRET`
  - the matching private key material
  - if you paste it into `.env`, keep the literal `\n` escapes or quote the
    multiline value so the loader preserves the PEM content

The recommended facilitator for this demo is:

- `https://api.cdp.coinbase.com/platform/v2/x402`

That is the authenticated CDP x402 facilitator. This repo still supports
unauthenticated facilitators such as `https://x402.org/facilitator`, but the
CDP path is the intended setup for a reliable wallet-to-wallet test.

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
APP_JWT_SECRET_KEY=dev-jwt-secret-key-with-32-bytes-min
APP_JWT_ACCESS_TOKEN_EXPIRY=900
APP_JWT_REFRESH_TOKEN_EXPIRY=604800
APP_SIWE_DOMAIN=127.0.0.1
APP_SIWE_NONCE_EXPIRY=300
APP_WALLET_CHANGE_COOLDOWN=604800
APP_API_KEY_PREFIX=amp_
APP_QUOTE_TTL_SECONDS=300
APP_X402_FACILITATOR_URL=https://api.cdp.coinbase.com/platform/v2/x402
APP_X402_NETWORK=base-sepolia
APP_X402_NETWORK_CAIP2=eip155:84532
APP_X402_CDP_API_KEY_ID=organizations/YOUR_ORG_ID/apiKeys/YOUR_KEY_ID
APP_X402_CDP_API_KEY_SECRET=-----BEGIN EC PRIVATE KEY-----\nYOUR_KEY_MATERIAL\n-----END EC PRIVATE KEY-----\n
APP_X402_PAY_TO_ADDRESS=0xYOUR_BASE_SEPOLIA_SETTLEMENT_ADDRESS
APP_PAYOUTS_ENABLED=false
APP_PAYOUTS_RPC_URL=https://sepolia.base.org
APP_PAYOUTS_CHAIN_ID=84532
APP_PAYOUTS_USDC_ADDRESS=0x0000000000000000000000000000000000000000
APP_PAYOUTS_WALLET_PRIVATE_KEY=0xYOUR_BASE_SEPOLIA_TREASURY_PRIVATE_KEY
APP_API_RATE_LIMIT=120/minute
APP_INVOKE_RATE_LIMIT=60/minute
APP_QUOTE_RATE_LIMIT=30/minute
APP_INVOKE_PAYLOAD_MAX_BYTES=1048576
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
- `APP_JWT_SECRET_KEY=dev-jwt-secret-key-with-32-bytes-min`
  - signing secret for marketplace JWTs
  - keep the example value only for local development
- `APP_JWT_ACCESS_TOKEN_EXPIRY=900`
  - access tokens expire after 15 minutes
- `APP_JWT_REFRESH_TOKEN_EXPIRY=604800`
  - refresh tokens expire after 7 days
- `APP_SIWE_DOMAIN=127.0.0.1`
  - the SIWE message domain the API expects during wallet login
  - for this local demo it should match the host portion of `API_BASE_URL`
- `APP_SIWE_NONCE_EXPIRY=300`
  - wallet-login nonces expire after 5 minutes
- `APP_WALLET_CHANGE_COOLDOWN=604800`
  - cooldown window for wallet rotation requests
- `APP_API_KEY_PREFIX=amp_`
  - prefix for generated marketplace API keys
- `APP_QUOTE_TTL_SECONDS=300`
  - quotes expire after 5 minutes
- `APP_X402_FACILITATOR_URL=https://api.cdp.coinbase.com/platform/v2/x402`
  - the recommended facilitator URL for the paid demo
  - uses CDP Bearer JWT auth for `/supported`, `/verify`, and `/settle`
- `APP_X402_NETWORK=base-sepolia`
  - human-readable network name used by the app
- `APP_X402_NETWORK_CAIP2=eip155:84532`
  - CAIP-2 network identifier for Base Sepolia
- `APP_X402_CDP_API_KEY_ID=organizations/YOUR_ORG_ID/apiKeys/YOUR_KEY_ID`
  - CDP secret API key id used to mint facilitator Bearer JWTs
- `APP_X402_CDP_API_KEY_SECRET=-----BEGIN EC PRIVATE KEY-----\nYOUR_KEY_MATERIAL\n-----END EC PRIVATE KEY-----\n`
  - CDP private key material used to sign facilitator JWTs
  - literal `\n` escapes are supported
- `APP_X402_PAY_TO_ADDRESS=0xYOUR_BASE_SEPOLIA_SETTLEMENT_ADDRESS`
  - marketplace settlement address advertised in the x402 payment requirement
  - must be a Base Sepolia EVM address
- `APP_PAYOUTS_ENABLED=false`
  - leave this `false` if you only want to test paid settlement
  - set it to `true` only when you also want to test provider payout execution
- `APP_PAYOUTS_RPC_URL=https://sepolia.base.org`
  - Base Sepolia RPC used for provider payout execution
- `APP_PAYOUTS_CHAIN_ID=84532`
  - chain id used for payout transactions
- `APP_PAYOUTS_USDC_ADDRESS=0x0000000000000000000000000000000000000000`
  - USDC token contract used for provider payout execution
- `APP_PAYOUTS_WALLET_PRIVATE_KEY=0xYOUR_BASE_SEPOLIA_TREASURY_PRIVATE_KEY`
  - treasury signer used when `POST /v1/provider/payouts` executes provider payouts
- `APP_API_RATE_LIMIT=120/minute`
  - base rate limit for authenticated API traffic
- `APP_INVOKE_RATE_LIMIT=60/minute`
  - invoke-specific rate limit
- `APP_QUOTE_RATE_LIMIT=30/minute`
  - quote-specific rate limit
- `APP_INVOKE_PAYLOAD_MAX_BYTES=1048576`
  - maximum allowed invoke payload size in bytes
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

Important:

- if you change any of `APP_DEMO_UPSTREAM_BASE_URL`, `APP_DEMO_FREE_UPSTREAM_PATH`,
  or `APP_DEMO_PAID_UPSTREAM_PATH`, rerun the seed step below before starting the
  API again
- the demo service stores upstream targets in the database, so changing `.env`
  alone is not enough

## Start the Mock Upstream

In one terminal:

```bash
make demo-upstream
```

This starts a FastAPI app on `http://127.0.0.1:9000` and logs every incoming header so you can inspect the `X-Agent-Marketplace-*` signing headers.

## Seed the Demo Data

In a second terminal, seed the demo service:

```bash
make seed
```

Expected output:

```text
provider_account_id=1
service_id=1
service_slug=demo-agent-service
free_endpoint_id=1
paid_endpoint_id=2
demo_upstream_base_url=http://127.0.0.1:9000
demo_free_upstream_path=/free-ping
demo_paid_upstream_path=/paid-summary
demo_provider_wallet=0x00000000000000000000000000000000000000a1
```

The seed now prepares only the provider-owned demo service. The example client
authenticates as whichever wallet matches `CONSUMER_PRIVATE_KEY`, so there is
no seeded consumer account id to export.

Pricing note:

- quote `amount_minor` is stored in USD minor units
- for example, `250` means `$2.50`
- the x402 payment requirement is converted internally to USDC base units before
  the facilitator sees it
- using the same example, `$2.50` becomes `2_500_000` base units for a 6-decimal
  USDC token
- the current seeded paid endpoint is configured at `25`, which means `$0.25`

## Start the Marketplace API

In a third terminal:

```bash
make demo-api
```

The API will be available at `http://127.0.0.1:8000`.

## Run the Example Client

In a fourth terminal:

```bash
export CONSUMER_PRIVATE_KEY=0xYOUR_BASE_SEPOLIA_PRIVATE_KEY
export API_BASE_URL=http://127.0.0.1:8000

make demo-client
```

What these values mean:

- `CONSUMER_PRIVATE_KEY`
  - buyer wallet private key for Base Sepolia
  - used by the example client for both wallet auth and x402 payment signing
- `API_BASE_URL`
  - local marketplace API base URL
  - the host should match `APP_SIWE_DOMAIN` unless you explicitly set `SIWE_DOMAIN`

The example client uses the installed Python x402 client and Base Sepolia network `eip155:84532`.

The example client will:

1. Call `GET /v1/auth/nonce` for the buyer wallet
2. Sign a SIWE message and call `POST /v1/auth/verify`
3. Call `GET /v1/services`
4. Call `POST /v1/services/demo-agent-service/quote`
5. Call `POST /v1/invoke/demo-agent-service` for the free endpoint
6. Call the paid endpoint, receive `402 Payment Required`, generate a signed payment with `x402HTTPClient`, and retry the same request

On success the example client also logs:

- the raw `PAYMENT-RESPONSE` header
- the decoded settlement payload
- the transaction hash you can inspect on Base Sepolia

After a successful paid retry, provider earnings are recorded internally as
`READY` payouts. If `APP_PAYOUTS_ENABLED=true`, authenticate as the provider and
call `POST /v1/provider/payouts` with an `Idempotency-Key` to execute the
provider transfer. Use `GET /v1/provider/payouts` to inspect the resulting
status change.

On failure the example client logs:

- the HTTP status code for the failed paid retry
- the full JSON body returned by the API

That makes it much easier to distinguish facilitator auth problems from verify
or settle failures.

## Optional API-Key Flow

If you want a long-lived token for repeated manual requests, first obtain a JWT
with the nonce and verify flow above, then create an API key:

```bash
curl -X POST http://127.0.0.1:8000/v1/auth/api-keys \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"name":"demo-cli"}'
```

The response includes the plaintext `api_key` once. You can then replace the
JWT with `Authorization: Bearer amp_...` for later quote or invoke calls.

## Expected Results

You should see:

- a discovered `demo-agent-service`
- a successful quote response
- a successful free invoke response
- an initial paid response with `402` and a `PAYMENT-REQUIRED` header
- a successful paid retry with `200`
- a `PAYMENT-RESPONSE` header
- a decoded settlement payload with `transaction` and `network`

When the paid retry succeeds, take the `transaction` value from the decoded
`PAYMENT-RESPONSE` and verify it on the Base Sepolia explorer:

- explorer: `https://sepolia-explorer.base.org`
- confirm the transaction hash exists
- confirm the buyer wallet is the payer
- confirm the settlement address matches `APP_X402_PAY_TO_ADDRESS`

If you later request provider payout execution, compare the treasury settlement
wallet and the provider wallet before and after `POST /v1/provider/payouts`.

If the facilitator cannot authenticate or settle, you will see the retry fail
with one of these app errors:

```text
502 {"detail":"facilitator authentication failed"}
502 {"detail":"facilitator unavailable"}
```

`facilitator authentication failed` usually means the CDP API key id or secret
is wrong for the configured facilitator URL. `facilitator unavailable` usually
means the facilitator could not be reached or returned a non-auth failure.

## Common Defaults

These are the values used by this repo unless you override them:

- code default facilitator URL: `https://x402.org/facilitator`
- recommended demo facilitator URL: `https://api.cdp.coinbase.com/platform/v2/x402`
- network name: `base-sepolia`
- network CAIP-2 id: `eip155:84532`
- local API URL: `http://127.0.0.1:8000`
- local SIWE domain: `127.0.0.1`
- local mock upstream URL: `http://127.0.0.1:9000`
- seeded service slug: `demo-agent-service`

## Troubleshooting

If `uv run pytest` fails on collection, the repo is configured to use `--import-mode=importlib` by default in `pyproject.toml`; just run `uv run pytest` normally.

If quote creation returns a validation error around `service_change_token`, rerun the demo seed command above. The seed now refreshes the seeded revision and change token on reruns.

If the paid retry fails before it reaches the app, confirm:

- `CONSUMER_PRIVATE_KEY` is set
- the example client's SIWE domain matches `APP_SIWE_DOMAIN`
- the app is returning `PAYMENT-REQUIRED`
- `examples/client.py` is using `http://127.0.0.1:8000`
- the buyer wallet is configured for Base Sepolia
- the buyer wallet has any required testnet funds for the path you are attempting

If the paid retry reaches the app but returns `502 {"detail":"facilitator authentication failed"}`, confirm:

- `APP_X402_FACILITATOR_URL` is `https://api.cdp.coinbase.com/platform/v2/x402`
- `APP_X402_CDP_API_KEY_ID` is the CDP secret API key id, not a wallet id
- `APP_X402_CDP_API_KEY_SECRET` matches that key id
- the secret formatting in `.env` preserved the PEM or base64 value correctly

If the paid retry reaches the app but returns `502 {"detail":"facilitator unavailable"}`, confirm:

- the facilitator URL is reachable from your machine
- CDP is not returning a transient `5xx`
- the buyer wallet has Base Sepolia ETH and USDC
- the settlement address is a valid Base Sepolia EVM address

If the paid retry returns a more specific body such as
`facilitator verify failed: ...` or `facilitator settle failed: ...`, use that
exact message as the source of truth. The API now preserves the facilitator's
error text instead of collapsing every non-auth failure into a generic `502`.

If the app cannot reach the mock upstream, confirm:

- `examples/mock_upstream.py` is running on port `9000`
- the API was started with `APP_DEMO_UPSTREAM_BASE_URL=http://127.0.0.1:9000`

If you want to try the unauthenticated public facilitator instead, set:

```dotenv
APP_X402_FACILITATOR_URL=https://x402.org/facilitator
APP_X402_CDP_API_KEY_ID=
APP_X402_CDP_API_KEY_SECRET=
```

That path can still exercise the x402 flow, but the CDP facilitator is the
recommended setup for a full buyer-to-provider settlement demo.

If the paid retry returns `502 {"detail":"facilitator unavailable"}`, the local demo still proved:

- the quote path works
- the free invoke path works
- the paid challenge is generated correctly
- the example x402 client signed and retried the paid request

That specific error means the external facilitator did not complete verify/settle for the retry request.
