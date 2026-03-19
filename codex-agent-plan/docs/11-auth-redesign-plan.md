# 11 — Account & Auth Redesign Plan

> Historical note
>
> This redesign plan is retained as an applied reference artifact. It explains
> the migration from legacy profile-based identity to the current unified
> account, API-key, and wallet-rotation model.

## Current State

- **3 identity tables**: `accounts` (id, is_admin, created_at), `provider_profiles`, `consumer_profiles` — separate one-to-one tables.
- **No real auth**: `X-Account-Id` header is trusted without verification.
- **No wallet support**: No wallet address stored anywhere in the backend.
- **No agent/human distinction**: No concept of account type.
- **Role determined by profile existence**: Provider capabilities gated by having a `provider_profile` row.

## Target State

- **1 unified account table** with profile data merged in.
- **Every account can provide and consume** — no role restrictions.
- **Wallet-based auth (SIWE)** for human users, issuing JWTs.
- **API key auth** for agents.
- **Both resolve to the same `ActorContext`** internally.
- **Wallet change flow** with two-step verification and audit log.
- `X-Account-Id` header removed entirely.

---

## 1. Database Schema Changes

### `accounts` table — unified identity

| Column              | Type           | Constraints                 | Notes                                      |
| ------------------- | -------------- | --------------------------- | ------------------------------------------ |
| `id`                | `BIGINT`       | PK, Identity(always=True)   | Unchanged                                  |
| `wallet_address`    | `VARCHAR(42)`  | UNIQUE, NOT NULL            | EVM checksummed address (0x...)            |
| `account_type`      | `VARCHAR(10)`  | NOT NULL, DEFAULT `'human'` | `'human'` or `'agent'`                     |
| `is_admin`          | `BOOLEAN`      | NOT NULL, DEFAULT `false`   | Unchanged                                  |
| `display_name`      | `VARCHAR(255)` | NOT NULL                    | Moved from profile tables                  |
| `nonce`             | `VARCHAR(64)`  | NOT NULL                    | For SIWE challenge-response                |
| `token_version`     | `INTEGER`      | NOT NULL, DEFAULT `1`       | Bumped on wallet change to invalidate JWTs |
| `wallet_changed_at` | `TIMESTAMPTZ`  | Nullable                    | NULL until first change; enforces cooldown |
| `created_at`        | `TIMESTAMPTZ`  | NOT NULL                    | Unchanged                                  |
| `updated_at`        | `TIMESTAMPTZ`  | NOT NULL                    | New                                        |

### New `api_keys` table — agent auth

| Column         | Type           | Constraints                            | Notes                            |
| -------------- | -------------- | -------------------------------------- | -------------------------------- |
| `id`           | `BIGINT`       | PK, Identity                           |                                  |
| `account_id`   | `BIGINT`       | NOT NULL, FK → `accounts.id` (CASCADE) |                                  |
| `key_hash`     | `VARCHAR(64)`  | UNIQUE, NOT NULL                       | SHA-256 hash of the key          |
| `key_prefix`   | `VARCHAR(8)`   | NOT NULL                               | First 8 chars for identification |
| `label`        | `VARCHAR(255)` | Nullable                               | Optional human-readable label    |
| `expires_at`   | `TIMESTAMPTZ`  | Nullable                               | NULL = no expiry                 |
| `revoked_at`   | `TIMESTAMPTZ`  | Nullable                               | NULL = active                    |
| `last_used_at` | `TIMESTAMPTZ`  | Nullable                               |                                  |
| `created_at`   | `TIMESTAMPTZ`  | NOT NULL                               |                                  |

### New `wallet_change_log` table — append-only audit trail

| Column        | Type          | Constraints                            | Notes |
| ------------- | ------------- | -------------------------------------- | ----- |
| `id`          | `BIGINT`      | PK, Identity                           |       |
| `account_id`  | `BIGINT`      | NOT NULL, FK → `accounts.id` (CASCADE) |       |
| `old_address` | `VARCHAR(42)` | NOT NULL                               |       |
| `new_address` | `VARCHAR(42)` | NOT NULL                               |       |
| `changed_at`  | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()`              |       |

### Tables to drop

- `provider_profiles`
- `consumer_profiles`

---

## 2. Auth Flow Design

### 2a. Human (SIWE) Flow

```
1. Client → GET /v1/auth/nonce?address=0x...
   ← { nonce: "random-string" }
   Server stores nonce on account row, or creates account if first time.

2. Client signs SIWE message containing nonce using wallet private key.

3. Client → POST /v1/auth/verify
   { message: "<SIWE message>", signature: "0x..." }
   Server verifies signature, checks nonce, issues tokens.
   ← { access_token: "jwt...", refresh_token: "jwt...", account: {...} }

4. Client → subsequent requests with Authorization: Bearer <access_token>

5. Client → POST /v1/auth/refresh
   { refresh_token: "jwt..." }
   ← { access_token: "new-jwt..." }
```

**JWT contents:**

```json
{
  "sub": "<account_id>",
  "wallet": "0x...",
  "tv": 1,
  "type": "access",
  "exp": 1234567890,
  "iat": 1234567890
}
```

- Access token: 15 minute expiry.
- Refresh token: 7 day expiry.

### 2b. Agent (API Key) Flow

```
1. Human account creates API key → POST /v1/auth/api-keys
   Authorization: Bearer <jwt>
   { label: "my-agent" }
   ← { key: "amp_abc123...", id: 1, key_prefix: "amp_abc1" }
   Full key shown ONCE, only hash stored.

2. Agent → subsequent requests with Authorization: Bearer amp_abc123...
   Server detects "amp_" prefix, hashes key, looks up in api_keys table,
   resolves to ActorContext.
```

### 2c. Auth Dependency Resolution (Unified)

```
Authorization header present?
├── Value starts with "amp_" → API key path
│   └── Hash key, lookup in api_keys table
│   └── Check not revoked, not expired
│   └── Update last_used_at
│   └── Resolve account → ActorContext(auth_method="api_key")
├── Otherwise → JWT path
│   └── Decode JWT, verify signature + expiry
│   └── Check token_version matches account's current token_version
│   └── Resolve account → ActorContext(auth_method="jwt")
└── No header → 401 Unauthorized
```

### 2d. Wallet Change Flow

```
1. User → POST /v1/account/wallet
   Authorization: Bearer <current valid JWT>
   { new_address: "0x..." }

   Server:
   - Verifies user is authenticated with current wallet.
   - Checks cooldown (wallet_changed_at + 7 days < now).
   - Checks new_address is not already used by another account.
   - Generates a nonce for the NEW wallet.
   ← { nonce: "...", challenge_expires_at: "..." }

2. User signs SIWE message with the NEW wallet's private key.

3. User → POST /v1/account/wallet/confirm
   Authorization: Bearer <current valid JWT>
   { message: "<SIWE signed by NEW wallet>", signature: "0x..." }

   Server:
   - Verifies current JWT still valid (proves ownership of old wallet).
   - Verifies SIWE signature (proves ownership of new wallet).
   - Writes row to wallet_change_log.
   - Updates wallet_address on account.
   - Bumps token_version (invalidates all existing JWTs).
   - Sets wallet_changed_at = now().
   - Returns new JWT set for the new wallet.
   ← { access_token: "...", refresh_token: "..." }
```

---

## 3. ActorContext Changes

```python
@dataclass(frozen=True, slots=True)
class ActorContext:
    account_id: int
    is_admin: bool = False
    account_type: str = "human"       # "human" | "agent"
    auth_method: str = "jwt"          # "jwt" | "api_key"
    wallet_address: str = ""          # always populated
```

All downstream services continue using `actor.account_id` as before. The new fields are additive and available for future policy decisions (e.g., rate-limiting agents differently).

---

## 4. Config Changes

New settings in `app/core/config.py`:

| Setting                    | Type  | Default           | Purpose                                 |
| -------------------------- | ----- | ----------------- | --------------------------------------- |
| `jwt_secret_key`           | `str` | required          | Secret for signing JWTs                 |
| `jwt_access_token_expiry`  | `int` | `900` (15 min)    | Access token lifetime in seconds        |
| `jwt_refresh_token_expiry` | `int` | `604800` (7 days) | Refresh token lifetime in seconds       |
| `siwe_domain`              | `str` | required          | Domain for SIWE message verification    |
| `siwe_nonce_expiry`        | `int` | `300` (5 min)     | Nonce validity window in seconds        |
| `wallet_change_cooldown`   | `int` | `604800` (7 days) | Minimum interval between wallet changes |
| `api_key_prefix`           | `str` | `"amp_"`          | Prefix for generated API keys           |

---

## 5. Route Changes

### New routes

| Method   | Path                         | Auth                         | Purpose                                 |
| -------- | ---------------------------- | ---------------------------- | --------------------------------------- |
| `GET`    | `/v1/auth/nonce`             | None                         | Get SIWE challenge nonce                |
| `POST`   | `/v1/auth/verify`            | None                         | Verify SIWE signature, issue JWTs       |
| `POST`   | `/v1/auth/refresh`           | None (refresh token in body) | Refresh access token                    |
| `POST`   | `/v1/auth/api-keys`          | JWT required                 | Create an API key                       |
| `GET`    | `/v1/auth/api-keys`          | JWT required                 | List account's API keys (metadata only) |
| `DELETE` | `/v1/auth/api-keys/{id}`     | JWT required                 | Revoke an API key                       |
| `GET`    | `/v1/account/me`             | CurrentActor                 | Get own account                         |
| `PATCH`  | `/v1/account/me`             | CurrentActor                 | Update display_name                     |
| `POST`   | `/v1/account/wallet`         | JWT required                 | Initiate wallet change                  |
| `POST`   | `/v1/account/wallet/confirm` | JWT required                 | Confirm wallet change                   |

### Removed routes

| Method  | Path               | Reason                              |
| ------- | ------------------ | ----------------------------------- |
| `POST`  | `/v1/providers`    | Account creation moves to auth flow |
| `GET`   | `/v1/providers/me` | Moves to `GET /v1/account/me`       |
| `PATCH` | `/v1/providers/me` | Moves to `PATCH /v1/account/me`     |
| `POST`  | `/v1/consumers`    | Account creation moves to auth flow |

---

## 6. Files to Create

| File                                                          | Purpose                                                              |
| ------------------------------------------------------------- | -------------------------------------------------------------------- |
| `app/api/routes/auth.py`                                      | Nonce, verify, refresh, API key CRUD routes                          |
| `app/api/routes/account.py`                                   | Account self-read, update, wallet change routes                      |
| `app/schemas/auth.py`                                         | Auth request/response schemas                                        |
| `app/schemas/account.py`                                      | Account response/update schemas                                      |
| `app/services/auth_service.py`                                | SIWE verification, JWT issuance, nonce management, API key lifecycle |
| `app/services/wallet_change_service.py`                       | Wallet change initiation and confirmation                            |
| `app/db/models/api_key.py`                                    | `ApiKey` ORM model                                                   |
| `app/db/models/wallet_change_log.py`                          | `WalletChangeLog` ORM model                                          |
| `app/repositories/api_key_repo.py`                            | API key CRUD                                                         |
| `app/repositories/wallet_change_log_repo.py`                  | Append-only log writes                                               |
| `app/core/security.py`                                        | JWT encode/decode, SIWE verification, API key hashing                |
| `alembic/versions/XXXX_unify_profiles_and_add_wallet_auth.py` | Migration                                                            |
| `tests/helpers/auth.py`                                       | Shared test helper for generating JWTs and API keys                  |
| `tests/unit/core/test_security.py`                            | JWT and SIWE helper tests                                            |
| `tests/unit/services/test_auth_service.py`                    | Auth service unit tests                                              |
| `tests/unit/services/test_wallet_change_service.py`           | Wallet change unit tests                                             |
| `tests/api/routes/test_auth_routes.py`                        | Auth route tests                                                     |
| `tests/api/routes/test_account_routes.py`                     | Account route tests                                                  |

## 7. Files to Modify

### Core changes

| File                               | Change                                                                                                            |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `app/db/models/account.py`         | Add `wallet_address`, `account_type`, `display_name`, `nonce`, `token_version`, `wallet_changed_at`, `updated_at` |
| `app/core/actor.py`                | Add `account_type`, `auth_method`, `wallet_address` fields                                                        |
| `app/api/deps/auth.py`             | Replace `X-Account-Id` header parsing with JWT/API key resolution                                                 |
| `app/api/deps/__init__.py`         | Update exports                                                                                                    |
| `app/core/config.py`               | Add JWT, SIWE, cooldown, API key settings                                                                         |
| `app/api/router.py`                | Mount `auth` and `account` routers; remove `providers`/`consumers` identity routes                                |
| `app/db/models/__init__.py`        | Export `ApiKey`, `WalletChangeLog`; remove `ProviderProfile`, `ConsumerProfile`                                   |
| `app/repositories/account_repo.py` | Add `get_by_wallet_address`, `update_nonce`, `update_wallet`, `bump_token_version`                                |
| `app/core/guardrails.py`           | Change rate limit key extraction from `X-Account-Id` header to resolved auth                                      |

### FK reference updates

| File                               | Change                                                                               |
| ---------------------------------- | ------------------------------------------------------------------------------------ |
| `app/db/models/service.py`         | `provider_account_id` FK changes from `provider_profiles.account_id` → `accounts.id` |
| `app/repositories/service_repo.py` | Remove any joins on `provider_profiles`                                              |

### Files to remove

| File                                        | Reason                                 |
| ------------------------------------------- | -------------------------------------- |
| `app/db/models/provider_profile.py`         | Merged into accounts                   |
| `app/db/models/consumer_profile.py`         | Merged into accounts                   |
| `app/schemas/provider.py`                   | Profile schemas no longer needed       |
| `app/schemas/consumer.py`                   | Profile schemas no longer needed       |
| `app/repositories/provider_profile_repo.py` | No more profile table                  |
| `app/repositories/consumer_profile_repo.py` | No more profile table                  |
| `app/services/provider_identity_service.py` | Profile creation replaced by auth flow |
| `app/services/consumer_identity_service.py` | Profile creation replaced by auth flow |
| `app/api/routes/providers.py`               | Identity routes move to `account.py`   |
| `app/api/routes/consumers.py`               | Identity routes move to `account.py`   |

### Test files (~25 files)

Every test file that creates `Account()` objects or uses `X-Account-Id` headers needs updating. Create a shared test helper (`tests/helpers/auth.py`) that generates valid JWTs and API keys for test accounts, then replace all `_auth_headers(account_id)` patterns.

| File                                                           | Change                             |
| -------------------------------------------------------------- | ---------------------------------- |
| `tests/api/routes/test_auth.py`                                | Rewrite for new auth flows         |
| `tests/api/routes/test_identity.py`                            | Rewrite for unified account routes |
| `tests/api/routes/test_admin.py`                               | Update auth helpers                |
| `tests/api/routes/test_provider_services.py`                   | Update auth helpers                |
| `tests/api/routes/test_invoke.py`                              | Update auth helpers                |
| `tests/api/routes/test_invoke_payment.py`                      | Update auth helpers                |
| `tests/api/routes/test_invoke_guardrails.py`                   | Update auth helpers                |
| `tests/api/routes/test_quotes.py`                              | Update account creation            |
| `tests/api/routes/test_finance.py`                             | Update auth helpers                |
| `tests/api/routes/test_discovery.py`                           | Update account creation            |
| `tests/api/routes/test_rate_limits.py`                         | Update header references           |
| `tests/integration/db/test_identity_models.py`                 | Update for unified model           |
| `tests/integration/db/test_migrations.py`                      | Update migration assertions        |
| `tests/integration/repositories/test_identity_repositories.py` | Update for unified repo            |
| `tests/unit/services/test_provider_identity_service.py`        | Remove or replace                  |
| `tests/unit/services/test_consumer_identity_service.py`        | Remove or replace                  |
| `tests/unit/services/test_provider_draft_service.py`           | Update `ActorContext`              |
| `tests/unit/services/test_invoke_service.py`                   | Update `ActorContext`              |
| `tests/unit/services/test_payment_service.py`                  | Update `ActorContext`              |
| `tests/unit/services/test_ledger_service.py`                   | Update `ActorContext`              |
| `tests/unit/services/test_moderation_service.py`               | Update `ActorContext`              |
| `tests/unit/services/test_publish_service.py`                  | Update account references          |
| `tests/unit/services/test_quote_service.py`                    | Update account references          |
| `tests/unit/services/test_revision_service.py`                 | Update account references          |
| `tests/unit/core/test_rate_limits_backend.py`                  | Update header references           |

---

## 8. New Dependencies

| Package       | Purpose                                                      |
| ------------- | ------------------------------------------------------------ |
| `siwe`        | SIWE message parsing and verification                        |
| `eth-account` | EVM account utilities (already in dev deps, promote to main) |
| `PyJWT`       | JWT encoding and decoding                                    |

---

## 9. Migration Details

A single Alembic migration executing in this order:

1. Add columns to `accounts`: `wallet_address` (nullable initially), `account_type`, `display_name` (nullable initially), `nonce`, `token_version`, `wallet_changed_at`, `updated_at`.
2. Backfill `display_name` from `provider_profiles` (falling back to `consumer_profiles`, then `'Anonymous'`).
3. Backfill `wallet_address` with generated placeholder addresses for existing rows (or leave NULL and require re-auth).
4. Make `display_name` NOT NULL.
5. Make `wallet_address` NOT NULL; add UNIQUE index.
6. Create `api_keys` table.
7. Create `wallet_change_log` table.
8. Update `services.provider_account_id` FK from `provider_profiles.account_id` → `accounts.id` (drop FK, re-add FK — safe since the values are identical).
9. Drop `provider_profiles` and `consumer_profiles` tables.

---

## 10. Suggested Branch Split

Per `AGENTS.md` guidance to keep branches narrow:

| Order | Branch                 | Scope                                                                                                        | Depends on             |
| ----- | ---------------------- | ------------------------------------------------------------------------------------------------------------ | ---------------------- |
| 1     | `feat/unified-profile` | Merge profiles into accounts, migration steps 1–5 and 8–9, update all FK references, update tests            | —                      |
| 2     | `feat/wallet-auth`     | SIWE auth, JWT issuance, `security.py`, auth dependency rewrite, nonce/verify/refresh routes, config changes | `feat/unified-profile` |
| 3     | `feat/api-key-auth`    | `api_keys` table, CRUD routes, auth dependency extension for API key path                                    | `feat/wallet-auth`     |
| 4     | `feat/wallet-change`   | `wallet_change_log` table, wallet change routes, `token_version` bump, cooldown enforcement                  | `feat/wallet-auth`     |

Branches 3 and 4 are independent of each other and can be worked in parallel once branch 2 lands.

---

## 11. Risks and Open Questions

| Risk                        | Mitigation                                                                                                                                                                                  |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Existing data migration** | If deployed, existing accounts need wallet backfill. Safest: make `wallet_address` nullable initially, require existing users to link a wallet on next visit.                               |
| **Service FK change**       | `services.provider_account_id` FK target changes. Safe since values are identical, but migration must drop and re-add FK in one transaction.                                                |
| **JWT secret rotation**     | Need a strategy for rotating `jwt_secret_key` without invalidating all sessions. Can use `token_version` or support multiple valid secrets. Defer to post-launch.                           |
| **SIWE library maturity**   | The `siwe` Python package is maintained but not heavily used. Alternative: verify EIP-191 signatures manually with `eth_account`. Worth evaluating during implementation.                   |
| **Nonce replay**            | Nonces must be single-use and time-limited. The nonce column on `accounts` handles this naturally (overwritten on each challenge), but concurrent requests from the same address need care. |
| **Test volume**             | ~25 test files need updates. Creating a shared test auth helper early in branch 1 reduces repetitive work.                                                                                  |
