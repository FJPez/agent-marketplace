# Agent Marketplace Backend

Agent Marketplace is a backend platform for publishing callable endpoints,
discovering them, and charging for access to them through a central service.
The primary use case is simple paid endpoint exposure: a provider can publish a
service, attach pricing to an endpoint, and let consumers pay to invoke it.

The platform is designed for both autonomous agents and human users. Agents can
integrate directly through the HTTP API for discovery, quoting, and invocation,
while human operators can manage services, payouts, and moderation through the
same authenticated workflows.

## Overview

Core capabilities:

- wallet-based authentication and API keys
- provider service authoring and publish control
- public discovery, schemas, and pricing lookups
- quote generation and invoke-time request binding
- free and paid invocation with x402-compatible payment handling
- moderation, earnings, ledger, and payout reporting

The detailed route contract lives in
[docs/api-reference.md](docs/api-reference.md) and
[docs/api-reference.pdf](docs/api-reference.pdf).

## Documentation Snapshot

- Main user-facing documentation lives under [docs/](docs/).
- The API reference is committed in both Markdown and styled PDF form at
  [docs/api-reference.md](docs/api-reference.md) and
  [docs/api-reference.pdf](docs/api-reference.pdf).
- The platform is backed by marketplace resource families including accounts,
  API keys, services, service endpoints, quotes, invocations, payouts, and
  moderation actions.
- The public API is workflow-oriented rather than a single textbook CRUD
  namespace: provider services cover create/list/detail/update, API keys cover
  create/list/revoke, and the remaining routes cover discovery, quoting,
  invoke, moderation, and payout flows.

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
TEST_REDIS_URL=redis://localhost:6379/0 make test
```

## Agent Integration

If you want to wire an agent into the platform, start with
[docs/agent-setup.md](docs/agent-setup.md). It covers:

- SIWE-style wallet authentication
- public discovery, schema, and pricing lookups
- quote creation for paid endpoints
- authenticated invocation with idempotency headers
- the `402 Payment Required` retry pattern for paid flows

Runnable companion scripts live in [examples/](examples/) and are summarized in
[examples/README.md](examples/README.md).

## Demo Paths

For a local-safe walkthrough that does not require funded wallets or a live
facilitator:

Before running the demo scripts, export two valid EVM private keys. They do not
need Base Sepolia funds unless you want to run the paid x402 settlement path.

```bash
export CONSUMER_PRIVATE_KEY=0xYOUR_LOCAL_CONSUMER_PRIVATE_KEY
export PROVIDER_PRIVATE_KEY=0xYOUR_LOCAL_PROVIDER_PRIVATE_KEY
export API_BASE_URL=http://127.0.0.1:8000
export SIWE_DOMAIN=127.0.0.1
```

```bash
make demo-upstream
make demo-api
uv run python examples/provider_publish.py
uv run python examples/minimal_consumer.py
```

For the full paid x402 and payout path, use
[docs/demo-setup.md](docs/demo-setup.md), then run:

```bash
make demo-client
make demo-provider
```

## Documentation

- [Documentation index](docs/README.md)
- [Agent setup guide](docs/agent-setup.md)
- [API reference source](docs/api-reference.md)
- [API reference PDF](docs/api-reference.pdf)
- [Full demo setup](docs/demo-setup.md)
- [Railway deployment guide](docs/deployment/railway.md)
- [Example scripts](examples/)
- Interactive FastAPI docs at `/docs` and `/redoc` once the app is running

Regenerate the styled API-reference PDF with:

```bash
make docs-pdf
```

## Environment Notes

- The local quick start assumes PostgreSQL and Redis are running.
- Paid x402 flows require Base Sepolia wallets, x402 facilitator credentials,
  and `APP_TREASURY_PRIVATE_KEY`.
- Railway deploys also require the treasury private key because the predeploy
  bootstrap marks that wallet as an admin account.
