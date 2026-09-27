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
- marketplace-issued provider signing secrets, rotatable with a grace period
- DNS proof that a provider controls its upstream hosts, checked at every publish
- provider payout addresses proven by signature, with payouts held after every proof
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
- **Signing secret keys.** Generate `APP_PROVIDER_SECRET_ENCRYPTION_KEYS` with the
  command in `.env.example` (see
  [Signing Secret Encryption Keys](#signing-secret-encryption-keys)) and set it as a
  shared variable used by both the API and the worker: in staging and production
  neither starts without it.

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
The migration that starts checking request schemas refuses to run while an
endpoint stores a request schema the invoke path cannot compile. The migration
that retires the `suspended` and `delisted` service lifecycle values (moderation
actions record those states) refuses to run while a service still holds one. In
any of these cases, drop and recreate the database:

```bash
docker compose exec -T postgres psql -U postgres -c "DROP DATABASE IF EXISTS agent_marketplace WITH (FORCE)"
docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE agent_marketplace"
uv run alembic upgrade head
```

To reload the demo data, set `APP_TREASURY_ADDRESS` (the paid demo endpoint's
`pay_to`, in `.env` or the environment), export `PROVIDER_PRIVATE_KEY` (the
demo provider's wallet key), and set `APP_PROVIDER_SECRET_ENCRYPTION_KEYS` (see
[Signing Secret Encryption Keys](#signing-secret-encryption-keys)); the demo
provider needs a signing secret before its listings can be invoked, and the seed
fails, naming that variable, without one. Then run `make seed` again; it prints
the demo provider's signing secret once, for local manual testing only.

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
  `APP_REDIS_URL`, an explicit `APP_SIWE_DOMAIN`, `APP_TREASURY_ADDRESS` and
  `APP_PROVIDER_SECRET_ENCRYPTION_KEYS`.
- A provider upstream must be an `https://` URL on port 443, without credentials,
  query string or fragment, whose host is a DNS name that resolves only to public
  addresses. This holds in every environment, so a local mock upstream cannot be
  registered through the API. The API resolves hosts with the system's nameservers.
  A service's upstreams can name at most 10 distinct hosts, which bounds the DNS
  queries each publish sends.
- Publishing checks live that every upstream host of the service carries the
  provider's TXT record at `_agent-marketplace.<host>`, with the value
  `POST /v1/provider/domain-verification` returns. A new or changed record can take
  minutes to be visible, longer after a failed check because resolvers cache the
  miss (negative caching), so publish again once it is.
- Every EVM address the API takes, from a wallet in a SIWE message to the treasury
  and asset settings, must be a lowercase `0x` followed by exactly 40 hex digits, with
  no surrounding whitespace. The digits may be all lowercase, all uppercase or EIP-55
  checksummed; a mixed-case address with a wrong checksum is refused as mistyped.
- An endpoint's `request_schema` is checked when it is saved. It must be a JSON
  Schema of draft 2020-12 throughout (a `$schema`, in any subschema, must name that
  draft), nest at most 32 levels, take at most 32768 bytes as compact JSON, and hold
  only valid Unicode and finite numbers. `$id` may appear only at the root, an anchor
  may be declared only once, and every `$ref` and `$dynamicRef` must be a `#` fragment
  naming a subschema of the same schema: the marketplace never fetches a remote
  schema. A schema has at most 64 patterns, each compiled by a linear-time engine
  within 10 KiB: a pattern that can apply to a request body must avoid lookaround and
  backreferences and compile no larger, so `\p{L}+` or `[A-Za-z0-9+/]{0,256}` are
  refused. `format` is an annotation only, as the draft specifies by default. Once the
  caller is known to own the service or endpoint, the schema is compiled in a request
  validation worker (below), because compiling some patterns takes time quadratic in
  their length. It must compile within `APP_REQUEST_SCHEMA_COMPILE_TIMEOUT_MS` (default
  100, at most half of `APP_REQUEST_VALIDATION_TIMEOUT_MS`), or it is refused with 422
  as too expensive to compile, so a worker that must compile a stored schema afresh
  still has at least half the validation deadline for the body. Saves never hold every
  worker: at most `APP_REQUEST_VALIDATION_WORKERS` minus one compiles run at once (one,
  with a single worker). A save can get 503 with `Retry-After: 1` when no worker is
  free, when workers cannot start, or while the API is shutting down.
- A request body is validated against its endpoint's `request_schema` in a worker
  process, because the validator (jsonschema-rs) holds the Python GIL and what a
  schema costs on a body is not known in advance. `APP_REQUEST_VALIDATION_WORKERS`
  (default 2, at most 32) workers start on the first compile or validation, each a
  fresh interpreter with an empty environment. The body is validated as the exact bytes
  the provider receives: it must be at most 1 MiB of UTF-8 JSON without a byte order
  mark, with no object holding a key twice, with finite numbers (not `NaN`, `Infinity`
  or `1e400`), nested at most 128 levels. A refused body names its first error and
  where it is, each cut to 200 characters. A validation must finish within
  `APP_REQUEST_VALIDATION_TIMEOUT_MS` (default 250, at most 10000); otherwise its
  worker is killed and the body is refused with 422 and the problem type
  `request_validation_timeout`. A body that makes its worker exit (a stack overflow,
  say) is refused with 422 and `request_validation_failed`. A body waits at most the
  same deadline for a free worker, then gets 503 with `Retry-After: 1`; 503 also means
  workers cannot start, or the API is shutting down, and, with the problem type
  `listing_unavailable` and `Retry-After: 60`, that the endpoint's stored schema no
  longer compiles. Each worker keeps its 256 most recently used compiled schemas and
  starts at about 25 MiB; once it passes 384 MiB it exits after answering, and the next
  call starts a fresh one. On Linux a worker can never pass 512 MiB of address space
  (macOS does not enforce that limit), so each API process needs room for
  `APP_REQUEST_VALIDATION_WORKERS` times 512 MiB.
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
- Provider signing secrets are stored encrypted with the Fernet keys in
  `APP_PROVIDER_SECRET_ENCRYPTION_KEYS` (comma-separated; no default; without one no
  signing secret can be issued). The first key encrypts and every listed key
  decrypts; see [Signing Secret Encryption Keys](#signing-secret-encryption-keys).
  A rotated-out secret keeps signing beside its replacement for
  `APP_PROVIDER_SECRET_GRACE_SECONDS` (default 86400, one day).
- If a rotate response is lost, retry with the same `Idempotency-Key` within 15
  minutes: the retry returns the secret that rotation issued instead of rotating
  again, which would end the grace of the secret the provider has deployed. After 15
  minutes the same key is refused with 409 and rotates nothing, so the key cannot
  read the deployed secret later; check `GET /v1/provider/signing-secret` and rotate
  with a new key if a new secret is still needed. Use a fresh random key, such as a
  UUID, for each rotation.
- A provider proves a payout address in two steps. First,
  `POST /v1/provider/payout-address/challenge` names the address and the payment
  network (`APP_PAYMENT_NETWORK`), and answers with EIP-712 typed data naming the
  provider's account, the address and the network under a one-time nonce, which
  expires after `APP_PAYOUT_ADDRESS_CHALLENGE_SECONDS` (default 300). Then the
  provider signs the typed data with that address's key (`eth_signTypedData_v4`) and
  submits the signature to `POST /v1/provider/payout-address`.
  `GET /v1/provider/payout-address` returns the latest proof. Wallets such as
  MetaMask sign v4 typed data only while connected to the chain it names (84532, Base
  Sepolia, by default), so switch the wallet to that network first. The address must
  be an externally owned account: a smart contract wallet cannot make the signature.
- Proofs are kept while the account exists, and the latest one decides where payouts
  go, but only `APP_PAYOUT_ADDRESS_HOLD_SECONDS` (default 86400, one day) after it:
  until then payouts are held, even if an earlier address, or the same one, was in
  use. Holds and challenge expiries are timed by the database's clock. The hold
  protects against a change the owner notices and acts on: a provider that sees a
  proof it did not make can prove its own address again, which supersedes it and
  restarts the hold. The marketplace does not yet notify providers of a change.
- The hold alone does not stop an account takeover. The payout routes, like changing
  the account's login wallet, need only a signed-in session, so an attacker with a
  stolen access token can move the login wallet to their own (which signs the owner
  out) and then prove their own payout address. Payouts are not sent yet; closing
  this gap is a follow-up that must land before they are.

## Signing Secret Encryption Keys

Generate a key with the command in `.env.example`:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- **Back up every listed key** outside the deployment. A stored secret decrypts
  only with the key it was encrypted under, so losing every listed key makes every
  stored secret unrecoverable: each provider must then rotate its secret and deploy
  the new one before the marketplace can sign its requests again.
- **To replace a key**, list the new one first and keep the old one after it. The
  new key encrypts every secret issued from then on, while each stored secret stays
  encrypted under the key it was issued with until its provider rotates it. Nothing
  re-encrypts stored secrets or reports which key each one uses yet, so keep every
  old key listed until a re-encryption command exists (a follow-up).
- **To remove a key** (after a compromise, say), take it out of the list. A provider
  whose current secret was encrypted under it cannot have its requests signed until
  it rotates. After it rotates, the new secret signs at once; the replaced secret
  cannot be decrypted, so it is skipped, with a warning in the logs, instead of
  signing during the grace period. Listing the old key again restores that grace.
