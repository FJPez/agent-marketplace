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
- [API reference source](docs/api-reference.md)
- [Agent setup guide](docs/agent-setup.md)
- [Full demo setup](docs/demo-setup.md)
- [Railway deployment guide](docs/deployment/railway.md)
- [Example scripts](examples/)
- [Contributor planning pack](codex-agent-plan/README.md)

The current runtime and integration docs live under `docs/`. The
`codex-agent-plan/` folder is retained as contributor planning context and
historical branch-handoff material.

## Environment Notes

- The local quick start assumes PostgreSQL and Redis are running.
- Paid x402 flows require Base Sepolia wallets, x402 facilitator credentials,
  and `APP_TREASURY_PRIVATE_KEY`.
- Railway deploys also require the treasury private key because the predeploy
  bootstrap marks that wallet as an admin account.
