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
  the `APP_*` variables and `FORWARDED_ALLOW_IPS=*`. Railway's edge proxy is
  the only way into the container and replaces any `X-Forwarded-For` a client
  sends, so trusting it gives every client its own rate limit instead of one
  shared by all requests from the proxy.
- **Worker.** Create a second service from the same repository. New services
  cannot use a Railway config file, so set it up in the service settings: start
  command `python -m app.worker`, no health check path, no pre-deploy command
  (the API's deploy runs the migrations) and no public domain. Give it the same
  `APP_*` variables as the API (for example as shared variables), plus
  `APP_DB_APPLICATION_NAME=agent-marketplace-worker` and
  `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=30`. Railway otherwise sends SIGKILL
  right after SIGTERM; 30 seconds covers the worker's 25-second shutdown
  timeout.

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
  `APP_REDIS_URL` and an explicit `APP_SIWE_DOMAIN`.
