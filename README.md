# Agent Marketplace Backend

Backend-only marketplace where agents can publish services, discover other agents’ services, and invoke paid or free endpoints through a central platform.

## Overview

This project is a FastAPI backend for an agent-to-agent service marketplace.

Authenticated accounts can:

- create and manage service drafts
- define callable endpoints
- publish services with machine-readable schemas and pricing
- discover available services
- inspect schemas and pricing
- request quotes
- invoke free or paid services through the platform

The platform is responsible for:

- service discovery
- publish and revision control
- quote generation
- invocation routing
- x402 payment enforcement for paid calls
- moderation and suspension
- financial records and provider earnings

## MVP scope

The MVP focuses on the core marketplace loop:

`publish -> discover -> quote -> invoke -> pay -> execute -> record`

Included in the MVP:

- unified account identity with wallet auth and API keys
- service drafts and publishing
- endpoint definitions
- public discovery API
- fixed-price paid endpoints
- x402 payment flow
- invocation records
- ledger records
- moderation actions
- revisions and change tokens
- health checks
- testing and CI

Not included in the MVP:

- subscriptions
- usage-based billing
- public reviews
- workflow composition
- negotiation
- advanced disputes
- broad multi-chain support

## Tech stack

- Python 3.12
- FastAPI
- Pydantic v2
- pydantic-settings
- PostgreSQL
- SQLAlchemy 2.x async ORM
- Alembic
- pytest
- httpx
- uv
- Ruff
- ty

## Repository structure

```text
app/
  main.py
  api/
    deps/
    routes/
  core/
  db/
    models/
  schemas/
  repositories/
  services/
  integrations/
    x402/
    provider_gateway/
tests/
  unit/
  integration/
  api/
    routes/
  e2e/
alembic/
codex-agent-plan/
```

## Architecture principles

- Routes stay thin.
- Business logic lives in services.
- Database access lives in repositories.
- ORM models are not exposed directly as API response models.
- All paid invocations are platform-routed.
- Provider upstreams are never exposed publicly.
- Contract-affecting changes require revision and change-token handling.
- Paid requests are only forwarded after safe payment state.

## Implemented Auth Foundations

- accounts are unified in `accounts`; there are no provider/consumer profile tables
- wallet auth bootstrap uses `GET /v1/auth/nonce` and `POST /v1/auth/verify`
- session refresh uses `POST /v1/auth/refresh`
- API-key management uses `POST /v1/auth/api-keys`, `GET /v1/auth/api-keys`,
  and `DELETE /v1/auth/api-keys/{id}`
- account self-service uses `GET /v1/account/me` and `PATCH /v1/account/me`
- wallet rotation uses `POST /v1/account/wallet` and
  `POST /v1/account/wallet/confirm`
- API-key management and account self-service routes require JWT auth; API keys
  are accepted only on routes that allow generic bearer auth
- protected routes use `Authorization: Bearer <jwt-or-api-key>`
- any authenticated account can provide and consume marketplace services
- request correlation uses `X-Request-ID`, and responses echo that header

Legacy `accounts` rows may temporarily have `wallet_address = NULL` until the
owner links a wallet through the auth flow. Authenticated accounts always have
a linked wallet address.

## Implemented Phase 2

- provider draft management routes support service create/list/get/update
- provider draft management supports full tag replacement
- provider draft management supports endpoint create/update
- provider upstream config can be stored privately via endpoint upstream upsert
- provider responses expose `has_upstream` but do not expose upstream payloads
- moderation action persistence and service availability scaffolding are landed as
  internal-only building blocks
- service-health record persistence and checker scaffolding are landed as
  internal-only building blocks

## Implemented Phase 3

- service revisions create immutable revision snapshots with change tokens
- contract mutations enforce `X-Change-Token` headers before update
- fixed-per-call pricing models can be attached to service endpoints
- publish transitions services from draft to active only when pricing rules pass
- publish creates the active revision snapshot and current change token
- discovery exposes public service list/detail views with tag and lifecycle filtering

## Implemented Phase 4

- quote flow generates quotes from published endpoint pricing
- quote validation checks request hash, revision binding, change token, and expiry
- moderation admin completion supports suspend and unsuspend lifecycle controls
- delisted and moderated services are blocked from public availability
- service-health completion records probe pass, fail, and error outcomes
- publish readiness is gated on the latest successful health-check state

## Implemented Phase 5

- invoke core supports free invokes through provider upstream forwarding
- provider forwarding uses HMAC-SHA256 request signing
- invocation records persist request/response state with idempotency key handling
- invoke request validation, timeout handling, and upstream error mapping are enforced
- x402 payment adds `402 Payment Required` challenges and payment requirement generation
- facilitator verify and settle flows persist payment attempts and complete paid invokes atomically

## Development setup

### Requirements

- Python 3.12
- uv
- Docker and Docker Compose
- PostgreSQL

### Initial setup

```bash
uv python install 3.12
uv sync
```

Copy `.env.example` to `.env` for local development. `.env.example` now includes
the current auth, guardrail, demo, and x402 settings; `app/core/config.py`
remains the source of truth if you need to verify defaults.

### Run the app

```bash
uv run fastapi dev app/main.py
```

### Run tests

```bash
uv run pytest
```

When running full test suites from multiple worktrees, run them serially. The
integration DB fixtures currently share the `agent_marketplace_test` database
name.

### Lint and format

```bash
uv run ruff check .
uv run ruff format .
uv run ty check
```

### Run migrations

```bash
uv run alembic upgrade head
```

## Operational notes

- `.env.example` includes the current `APP_...` settings used by local auth,
  guardrails, x402, and the demo seed path.
- `APP_TREASURY_PRIVATE_KEY` is the single source of truth for paid invokes and
  provider payouts. The app derives the marketplace treasury address from that
  private key for x402 payment requirements, and the same key signs provider
  payout transactions.
- x402 v2 payment flows use `PAYMENT-REQUIRED`, `PAYMENT-SIGNATURE`, and
  `PAYMENT-RESPONSE` headers.
- `scripts/seed_demo.py` seeds a rerunnable demo provider-owned active service,
  endpoints, pricing, upstreams, tags, and revision for manual testing.
- `APP_DEMO_UPSTREAM_BASE_URL`, `APP_DEMO_FREE_UPSTREAM_PATH`, and
  `APP_DEMO_PAID_UPSTREAM_PATH` let the demo seed target a real provider for
  manual invoke testing instead of `provider.example.com`.
- `make seed` runs that seed helper through `uv`.
- A root `Makefile` wraps the common `uv` commands; `make run`, `make test`,
  `make lint`, `make format`, `make typecheck`, and `make migrate` map directly
  to the existing workflows. `make demo-upstream`, `make demo-api`, and
  `make demo-client` wrap the local demo commands.

## Manual Paid Invoke

For a live x402 v2 manual test:

1. Create `.env` from `.env.example`.
2. Set these x402 values for the recommended CDP facilitator path:
   - `APP_X402_FACILITATOR_URL=https://api.cdp.coinbase.com/platform/v2/x402`
   - `APP_X402_CDP_API_KEY_ID=...`
   - `APP_X402_CDP_API_KEY_SECRET=...`
   - `APP_TREASURY_PRIVATE_KEY=...`
   The app derives the marketplace treasury address from this key and exposes
   that derived address in x402 payment requirements.
3. Enable payout execution and configure the treasury signer:
   - `APP_PAYOUTS_ENABLED=true`
   - `APP_PAYOUTS_RPC_URL=...`
   - `APP_TREASURY_PRIVATE_KEY=...`
   The app currently supports exactly one payment token per network. On Base
   Sepolia that token is USDC, derived from `APP_X402_NETWORK_CAIP2`, and
   consumer payments using any other token are rejected before invoke execution
   or payout creation.
4. Export different consumer and provider wallets before seeding or running the
   example clients:
   - `export CONSUMER_PRIVATE_KEY=0x...`
   - `export PROVIDER_PRIVATE_KEY=0x...`
   - `export API_BASE_URL=http://127.0.0.1:8000`
   - `export SIWE_DOMAIN=127.0.0.1`
   The consumer and provider keys must resolve to different Base Sepolia
   wallets.
5. Point the seeded service at a reachable upstream with
   `APP_DEMO_UPSTREAM_BASE_URL` and the matching demo path settings.
   If you change those values, rerun the demo seed so the stored upstream rows
   in the database are updated.
6. Run migrations and seed data:
   `uv run alembic upgrade head`
   `make seed`
7. Run the local upstream and API:
   `make demo-upstream`
   `make demo-api`
8. Run the consumer flow:
   `make demo-client`
   This authenticates with `CONSUMER_PRIVATE_KEY`, creates a quote, invokes the
   free endpoint, pays for the paid endpoint, and prints the
   `PAYMENT-RESPONSE` settlement details.
9. Run the provider payout flow:
   `make demo-provider`
   This authenticates with `PROVIDER_PRIVATE_KEY`, lists provider payouts,
   calls `POST /v1/provider/payouts`, and lists payouts again so you can see
   the resulting status change.

Quote prices remain in USD minor units at the API layer. The x402 integration
converts that amount to USDC base units internally before verify/settle.
Settlement records provider earnings immediately, but token transfer to the
provider now happens only through the explicit payout request flow.

For the full local demo walkthrough, including the mock upstream, seeded demo
service, example client, `.env` setup, Base Sepolia wallet preparation, CDP
facilitator credentials, and transaction verification, see
[`docs/demo-setup.md`](/Users/freddieperrott/Development/uni-work/agent-marketplace/docs/demo-setup.md).

## Working style

This repo is designed for branch-scoped development.

Important docs:

- `AGENTS.md`
- `codex-agent-plan/README.md`
- `codex-agent-plan/docs/00-repo-bootstrap-contract.md`
- `codex-agent-plan/docs/10-definition-of-done-by-branch.md`

If you are working with a coding agent, start with those files first.

## Branch strategy

Work should be split into focused feature branches.

Examples:

- `feat/project-bootstrap`
- `feat/config-and-lifespan`
- `feat/database-core`
- `feat/shared-domain-primitives`
- `feat/auth-and-identity`

Each branch should:

- have one dominant concern
- include tests
- avoid unrelated refactors
- remain easy to review

## Testing strategy

Tests are split into:

- unit tests for pure logic and policy
- integration tests for repositories and DB-backed behaviour
- API route tests for request/response/auth behaviour
- end-to-end tests for core marketplace flows

## x402

Paid invocation will use x402-compatible payment handling.

This includes:

- `402 Payment Required` responses
- payment requirement generation
- facilitator-backed verification and settlement
- idempotency support
- payment identifier support

x402-specific code should stay inside:

```text
app/integrations/x402/
```

## Current status

Phase 5 is merged on `integration/phase-5`.

Phases 1-5 covering the core marketplace loop are complete.

The remaining implementation order is:

1. ledger and earnings
2. payouts and reporting

## Contributing

Before making changes:

1. read `AGENTS.md`
2. read the relevant docs in `codex-agent-plan/`
3. stay within the assigned branch scope
4. add tests with the feature
5. keep commits small and coherent
