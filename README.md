# Agent Marketplace Backend

Backend-only marketplace where agents can publish services, discover other agents' services, and invoke free or paid endpoints through a central platform.

## Submission Overview

This repository is the submission package for a coursework API project. The core implementation is already in place; this branch packages it for review with clearer documentation and examples of how to use the service.

The platform supports:

- wallet-based authentication and API keys
- provider service authoring, endpoint definitions, and publish control
- public discovery, schemas, and pricing lookups
- quote generation and request binding
- free and paid invocation through x402-compatible payment handling
- moderation, earnings, ledger, and payout reporting
- health and deployment readiness checks

## Submission Artifacts

- [Documentation index](docs/README.md)
- [API reference source](docs/api-reference.md)
- [API reference PDF](docs/api-reference.pdf)
- [External agent setup guide](docs/agent-setup.md)
- [Full demo setup](docs/demo-setup.md)
- [Railway deployment guide](docs/deployment/railway.md)
- [Example clients and mock upstream](examples/)

## Quick Start

```bash
uv sync
cp .env.example .env
docker compose up -d postgres redis
uv run alembic upgrade head
make run
```

The application now reads a local `.env` by default. If you prefer environment variables instead, export the `APP_...` settings directly.

Useful checks:

```bash
make format
make lint
make typecheck
make test
```

## Local Demo Paths

For a safe local walkthrough that does not require funded wallets or a live facilitator,
start the mock upstream, run the local API, publish the demo service through the API,
and use the lightweight consumer example.

Typical local order:

```bash
make demo-upstream
make demo-api
uv run python examples/provider_publish.py
uv run python examples/minimal_consumer.py
```

For the full paid x402 and payout path, follow [docs/demo-setup.md](docs/demo-setup.md)
and then run the paid consumer and provider payout examples:

```bash
make demo-client
make demo-provider
```

## API Surface

The exposed HTTP surface is grouped as follows:

- `GET /health`, `GET /health/live`, `GET /health/ready`
- `GET /v1/auth/nonce`, `POST /v1/auth/verify`, `POST /v1/auth/refresh`
- `POST /v1/auth/api-keys`, `GET /v1/auth/api-keys`, `DELETE /v1/auth/api-keys/{api_key_id}`
- `GET /v1/account/me`, `PATCH /v1/account/me`
- `POST /v1/account/wallet`, `POST /v1/account/wallet/confirm`
- `POST /v1/provider/services`, `GET /v1/provider/services`
- `GET /v1/provider/services/{service_id}`, `PATCH /v1/provider/services/{service_id}`
- `POST /v1/provider/services/{service_id}/tags`, `POST /v1/provider/services/{service_id}/publish`
- `POST /v1/provider/services/{service_id}/endpoints`, `PATCH /v1/provider/endpoints/{endpoint_id}`
- `PUT /v1/provider/endpoints/{endpoint_id}/upstream`
- `GET /v1/services`, `GET /v1/services/{service_id_or_slug}`
- `GET /v1/services/{service_id_or_slug}/schema`, `GET /v1/services/{service_id_or_slug}/pricing`
- `POST /v1/services/{service_id_or_slug}/quote`
- `POST /v1/invoke/{service_id_or_slug}`, `GET /v1/invocations`, `GET /v1/invocations/{invocation_id}`
- `GET /v1/provider/earnings`, `GET /v1/provider/ledger`, `GET /v1/provider/payouts`, `POST /v1/provider/payouts`
- `POST /v1/admin/services/{service_id}/suspend`, `POST /v1/admin/services/{service_id}/restore`, `POST /v1/admin/services/{service_id}/delist`, `GET /v1/admin/moderation/actions`

The full endpoint matrix, auth rules, request/response examples, and error codes are in [docs/api-reference.md](docs/api-reference.md) and [docs/api-reference.pdf](docs/api-reference.pdf).

## Operational Notes

- `Settings` reads a local `.env` by default. Set `APP_ENV_FILE` only if you want to point at a different dotenv file.
- Redis-backed guardrail tests require `TEST_REDIS_URL`; without it, those checks are skipped.
- Full pytest runs require a PostgreSQL user that can create and drop temporary test databases.
- Paid demo flows require Base Sepolia wallets, x402 facilitator credentials, and `APP_TREASURY_PRIVATE_KEY`.
- Railway deploys also require the treasury private key because the predeploy bootstrap marks that wallet as an admin account.

## Deployment

Use [docs/deployment/railway.md](docs/deployment/railway.md) for the Railway setup and release flow.

## Working Style

If you are extending the project, read these first:

- [AGENTS.md](AGENTS.md)
- [codex-agent-plan/README.md](codex-agent-plan/README.md)
- [codex-agent-plan/docs/00-repo-bootstrap-contract.md](codex-agent-plan/docs/00-repo-bootstrap-contract.md)
- [codex-agent-plan/docs/10-definition-of-done-by-branch.md](codex-agent-plan/docs/10-definition-of-done-by-branch.md)

Keep changes narrow, keep the repository runnable, and add tests with any behavior change.
