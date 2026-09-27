# Agent Marketplace Backend

Agent Marketplace is a backend platform for publishing callable endpoints,
discovering them, and charging for access to them through a central service.
Providers publish services with free or paid endpoints; consumers, including
autonomous agents, discover them over the HTTP API.

## Status

The invocation and payment layer is being rebuilt from scratch around x402 v2.
Until it lands, the API covers identity, provider authoring, publishing,
moderation and discovery, and there is no invoke endpoint.

## Overview

Core capabilities:

- wallet-based authentication (SIWE) and API keys
- provider service authoring and publish control
- public discovery, schemas, and pricing lookups
- moderation for administrators

## Quick Start

```bash
uv sync
cp .env.example .env
docker compose up -d postgres redis
uv run alembic upgrade head
make run
```

The application reads a local `.env` by default. If you prefer environment
variables instead, export the `APP_...` settings directly.

Useful verification commands:

```bash
make format
make lint
make typecheck
TEST_REDIS_URL=redis://localhost:6379/15 make test
```

The tests flush the Redis database named by `TEST_REDIS_URL`, so point it at a
database you use for nothing else.

DB-backed tests skip when PostgreSQL is unreachable. Set
`APP_TEST_REQUIRE_DATABASE=1` to make them fail instead; CI does. Each test run
creates its own `agent_marketplace_test_*` databases, drops them at the end, and
at start drops those left behind by runs that were killed before teardown.

Tests marked `e2e` run against live services and are deselected by default; run
them with `uv run pytest -m e2e`.

## Worker

`make worker` runs the background worker (`python -m app.worker`) against the
same settings as the API. It runs the registered loops until SIGTERM or SIGINT;
no loop is registered yet (the recovery loops arrive with the paid invocation
lifecycle). On a signal it starts no new iteration and gives a running one
`APP_WORKER_SHUTDOWN_TIMEOUT_SECONDS` (default 25) to finish before cancelling
it.

## Deployment

Both processes run from the one Docker image: the API with the image's default
command, the worker with `python -m app.worker`.

`make docker-run` builds the image and starts PostgreSQL, Redis, the API on
`http://127.0.0.1:18000` and the worker; `make docker-stop` stops them.

The API trusts `X-Forwarded-For` only from the addresses in
`FORWARDED_ALLOW_IPS` (default `127.0.0.1`), so a published container ignores
forged headers and rate limits key on the connecting address.

### Railway

Railway runs two services from this repository, both built from the
`Dockerfile`:

- **API.** Configured by `railway.toml`: the `/health/ready` health check and a
  pre-deploy command that migrates the database and bootstraps the admin. Set
  the `APP_*` variables, `FORWARDED_ALLOW_IPS=*` (only while the check below
  passes) and `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=30`. Railway otherwise sends
  SIGKILL right after SIGTERM, cutting off the requests in flight at every
  deploy; with 30 seconds, uvicorn finishes them before it exits.
- **Worker.** Create a second service from the same repository. New services
  cannot use a Railway config file, so set it up in the service settings: start
  command `python -m app.worker`, no health check path, no pre-deploy command
  (the API's deploy runs the migrations) and no public domain. Give it the same
  `APP_*` variables as the API (for example as shared variables), plus
  `APP_DB_APPLICATION_NAME=agent-marketplace-worker` and
  `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=30`. Railway otherwise sends SIGKILL
  right after SIGTERM; 30 seconds covers the worker's 25-second shutdown
  timeout.

Railway's config as code (`railway.toml`) is deprecated, and Railway stops
reading it on 2026-12-01. After that the API would deploy without its health
check and without the pre-deploy migration, so a deploy could go live on an
unmigrated schema. Before then, move both services to Railway's infrastructure
as code (`.railway/railway.ts`), after confirming it can express the pre-deploy
command and the draining time.

#### Checking the proxy trust after a deploy

With `FORWARDED_ALLOW_IPS=*`, uvicorn takes the client address from the
leftmost `X-Forwarded-For` entry, so each client gets its own rate limit rather
than one shared by every request from Railway's edge proxy. That is safe only
if the edge replaces the `X-Forwarded-For` a client sends, and Railway's own
statements conflict: its staff said in 2024 that the edge appends to the
header, and in 2026-06 that it strips it. If it appends, any client can forge a
new address on every request and escape the rate limit. So this check is
required on a staging deploy before production relies on
`FORWARDED_ALLOW_IPS=*`:

1. Set `APP_API_RATE_LIMIT=2/minute` and deploy.
2. Send 5 unauthenticated `/v1` requests, each with a different forged
   `X-Forwarded-For`:

   ```bash
   for i in 1 2 3 4 5; do
     curl -s -o /dev/null -w '%{http_code}\n' \
       -H "X-Forwarded-For: 203.0.113.$i" "https://$STAGING_DOMAIN/v1/services"
   done
   ```

3. Expect `429` from the third request on. If the requests are not limited,
   the forged header reaches uvicorn: do not keep `FORWARDED_ALLOW_IPS=*`. The
   rate limit then has to key on a header the edge overwrites, which is a
   follow-up.

## Resetting a Local Database

The migration history was squashed into a single baseline on 2026-09-26. A
database created before then cannot be upgraded. The migration that replaces the
USD-cent `endpoint_prices` with `listing_prices` refuses to run while
`endpoint_prices` still holds rows (for example from an earlier `make seed`),
because a cent price has no asset, network or treasury to become a price version.
In either case, drop and recreate the database, then run `make seed` again if you
use the demo data:

```bash
docker compose exec -T postgres psql -U postgres -c "DROP DATABASE IF EXISTS agent_marketplace WITH (FORCE)"
docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE agent_marketplace"
uv run alembic upgrade head
```

## Admin Bootstrap

`scripts/bootstrap_admin.py` makes the wallet in `APP_BOOTSTRAP_ADMIN_WALLET` an
administrator, creating its account if needed. Railway runs it before every
deploy, so set `APP_BOOTSTRAP_ADMIN_WALLET` there alongside `APP_DATABASE_URL`.
The script reads both variables from the environment, not from `.env`.

```bash
APP_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agent_marketplace APP_BOOTSTRAP_ADMIN_WALLET=0xYourAdminWallet make bootstrap-admin
```

## Environment Notes

- The local quick start assumes PostgreSQL and Redis are running.
- Application logs are single-line JSON on stdout, at `APP_LOG_LEVEL` (default
  `INFO`), with the request's `request_id`. Values of the `Authorization`,
  `Cookie`, `PAYMENT-SIGNATURE` and `X-PAYMENT` headers are redacted. A client's
  `X-Request-ID` is kept only if it is 1 to 128 letters, digits, `.`, `_`, `:`
  or `-`; otherwise the API generates one.
- Staging and production require a non-local `APP_DATABASE_URL`,
  `APP_REDIS_URL`, an explicit `APP_SIWE_DOMAIN` and `APP_TREASURY_ADDRESS`.
- Each new price version records the payment terms current when it is created:
  the treasury `APP_TREASURY_ADDRESS` as `pay_to` (no default; without it no
  paid price can be set), `APP_PAYMENT_NETWORK` (default `eip155:84532`, Base
  Sepolia), `APP_PAYMENT_ASSET` (default Base Sepolia USDC,
  `0x036CbD53842c5426634e7929541eC2318f3dCF7e`), `APP_PAYMENT_MAX_TIMEOUT_SECONDS`
  (default 120, at most 3600) and `APP_PLATFORM_FEE_BPS` (default 1000, 10%).
  Prices are in atomic units of the asset (1 USDC = 1,000,000) and must be at
  least `APP_MIN_PRICE_AMOUNT` (default 10000, 0.01 USDC). Changing a setting
  affects only price versions created afterwards; a provider moves a listing onto
  the current terms by resending its price, which creates a new version.
