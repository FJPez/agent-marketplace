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

## Resetting a Local Database

The migration history was squashed into a single baseline on 2026-09-26. A
database created before then cannot be upgraded; drop and recreate it:

```bash
docker compose exec -T postgres psql -U postgres -c "DROP DATABASE IF EXISTS agent_marketplace WITH (FORCE)"
docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE agent_marketplace"
uv run alembic upgrade head
```

## Admin Bootstrap

`scripts/bootstrap_admin.py` makes the wallet in `APP_BOOTSTRAP_ADMIN_WALLET` an
administrator, creating its account if needed. Railway runs it before every
deploy, so set `APP_BOOTSTRAP_ADMIN_WALLET` there alongside `APP_DATABASE_URL`.

```bash
APP_BOOTSTRAP_ADMIN_WALLET=0xYourAdminWallet make bootstrap-admin
```

## Environment Notes

- The local quick start assumes PostgreSQL and Redis are running.
- Staging and production require a non-local `APP_DATABASE_URL`,
  `APP_REDIS_URL` and an explicit `APP_SIWE_DOMAIN`.
