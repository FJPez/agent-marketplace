# Agent Setup Guide

This guide is the main onboarding path for integrating an agent with the
marketplace API. Use it when you want an agent to authenticate, discover
services, create quotes, and invoke endpoints.

Companion references:

- [API reference](api-reference.md)
- [Example scripts](../examples/README.md)
- [Full paid demo setup](demo-setup.md)

## Consumer Flow

The typical consumer path is:

1. authenticate with a wallet
2. discover services and inspect schemas/pricing
3. create a quote for the exact paid payload you intend to send
4. invoke an endpoint with `Authorization` and `Idempotency-Key`
5. for paid endpoints, handle the `402 Payment Required` retry flow

## 1. Authenticate

The API uses SIWE-style wallet authentication.

```python
from datetime import UTC, datetime

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

API_BASE_URL = "http://127.0.0.1:8000"
SIWE_DOMAIN = "127.0.0.1"
CHAIN_ID = 84532


async def authenticate(client: httpx.AsyncClient, private_key: str) -> str:
    wallet_address = Account.from_key(private_key).address

    nonce_response = await client.get(
        "/v1/auth/nonce",
        params={"address": wallet_address},
    )
    nonce_response.raise_for_status()
    nonce = nonce_response.json()["nonce"]

    issued_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    message = "\n".join(
        [
            f"{SIWE_DOMAIN} wants you to sign in with your Ethereum account:",
            wallet_address,
            "",
            f"URI: {API_BASE_URL}",
            "Version: 1",
            f"Chain ID: {CHAIN_ID}",
            f"Nonce: {nonce}",
            f"Issued At: {issued_at}",
        ]
    )

    signed = Account.sign_message(
        encode_defunct(text=message),
        private_key=private_key,
    )

    verify_response = await client.post(
        "/v1/auth/verify",
        json={
            "message": message,
            "signature": signed.signature.to_0x_hex(),
        },
    )
    verify_response.raise_for_status()
    return verify_response.json()["access_token"]
```

Successful verification returns both `access_token` and `refresh_token`. For
consumer invoke flows, the access token is enough.

If you want a long-lived agent credential instead of carrying a JWT, use
[examples/api_key_client.py](../examples/api_key_client.py). It shows the
JWT-to-API-key flow, generic bearer usage, JWT-only route rejection, and key
revocation.

## 2. Discover Services

Discovery is public, so an agent can inspect the catalogue before invoking
anything.

```python
async def load_service_views(client: httpx.AsyncClient, service_slug: str) -> dict[str, object]:
    services = (await client.get("/v1/services")).json()
    detail = (await client.get(f"/v1/services/{service_slug}")).json()
    schema = (await client.get(f"/v1/services/{service_slug}/schema")).json()
    pricing = (await client.get(f"/v1/services/{service_slug}/pricing")).json()

    return {
        "services": services,
        "detail": detail,
        "schema": schema,
        "pricing": pricing,
    }
```

Use the schema and pricing responses to decide:

- which endpoint key to invoke
- whether the endpoint is free or paid
- what payload shape is expected

## 3. Create a Quote

Quotes matter for paid endpoints because they bind the exact payload, service
revision, and change token used during invoke.

```python
async def create_quote(
    client: httpx.AsyncClient,
    service_slug: str,
    payload: object,
) -> dict[str, object]:
    response = await client.post(
        f"/v1/services/{service_slug}/quote",
        json={
            "endpoint_key": "paid-summary",
            "payload": payload,
        },
    )
    response.raise_for_status()
    return response.json()
```

If the payload changes after quoting, the quote should be treated as stale and
re-created.

`payload` can be any JSON value allowed by the endpoint schema, not only an
object. For example, an endpoint may accept an array payload and return a JSON
string response.

## 4. Invoke an Endpoint

Every invoke request needs:

- `Authorization: Bearer <jwt-or-api-key>`
- `Idempotency-Key: <unique value>`

Free invoke example:

```python
import uuid


async def invoke_free(
    client: httpx.AsyncClient,
    access_token: str,
    service_slug: str,
) -> dict[str, object]:
    response = await client.post(
        f"/v1/invoke/{service_slug}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Idempotency-Key": f"consumer-{uuid.uuid4()}",
        },
        json={
            "endpoint_key": "free-ping",
            "payload": {"message": "hello from an agent client"},
            "quote_id": None,
        },
    )
    response.raise_for_status()
    return response.json()
```

## 5. Handle Paid Invokes

Paid invokes use the same route, but the first attempt may return
`402 Payment Required`.

```python
async def begin_paid_invoke(
    client: httpx.AsyncClient,
    access_token: str,
    service_slug: str,
    quote_id: int,
    payload: object,
) -> httpx.Response:
    return await client.post(
        f"/v1/invoke/{service_slug}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Idempotency-Key": "consumer-paid-demo-001",
        },
        json={
            "endpoint_key": "paid-summary",
            "payload": payload,
            "quote_id": quote_id,
        },
    )
```

If that response is `402`, inspect:

- `PAYMENT-REQUIRED`
- `X-Request-ID`

The full x402 settlement and retry flow is already implemented in
[examples/client.py](../examples/client.py). Use that script when you want a
runnable paid example instead of wiring the x402 client yourself.

## 6. Read Invocation Results

If you want to inspect invocation history or fetch a specific result later, use
the invocation routes:

```python
async def load_invocation_views(
    client: httpx.AsyncClient,
    access_token: str,
    invocation_id: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    headers = {"Authorization": f"Bearer {access_token}"}
    invocation_list = (await client.get("/v1/invocations", headers=headers)).json()
    invocation_detail = (
        await client.get(f"/v1/invocations/{invocation_id}", headers=headers)
    ).json()
    return invocation_list, invocation_detail
```

This is useful when your agent wants to store the `invocation_id` returned by
`POST /v1/invoke/...` and read it back later.

## 7. Minimal End-to-End Example

For a lightweight local-safe consumer flow, use
[examples/minimal_consumer.py](../examples/minimal_consumer.py).

For the corresponding provider setup path, use
[examples/provider_publish.py](../examples/provider_publish.py).

These two scripts are the quickest way to get a local agent-to-service demo
working without the full paid settlement path.

## 8. Provider Notes

Providers use the same wallet-auth flow, then manage services through the
provider routes:

1. `POST /v1/provider/services`
2. `POST /v1/provider/services/{service_id}/endpoints`
3. `PUT /v1/provider/endpoints/{endpoint_id}/upstream`
4. `POST /v1/provider/services/{service_id}/tags`
5. `POST /v1/provider/services/{service_id}/publish`

When the marketplace forwards a request upstream, it signs the request with:

- `X-Agent-Marketplace-Key-Id`
- `X-Agent-Marketplace-Timestamp`
- `X-Agent-Marketplace-Request-Hash`
- `X-Agent-Marketplace-Invocation-Id`
- `X-Agent-Marketplace-Signature`

Treat `X-Agent-Marketplace-Invocation-Id` as the upstream idempotency key for
provider execution. When a client retries the same marketplace invoke, the
provider should replay by that header instead of re-executing the side effect.

Provider upstream `path` values must start with `/` and must not include a
scheme, host, query string, or fragment.

Upstream URLs and credentials remain private and are not exposed in public
discovery responses.

Wallet rotation and admin moderation are documented in the API reference, but
they are intentionally not part of the default runnable demo set in
`examples/`.
