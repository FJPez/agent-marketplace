from __future__ import annotations

import argparse
import os
import sys
from json import JSONDecodeError
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_NONCE_ADDRESS = "0x0000000000000000000000000000000000000001"
DEFAULT_PUBLIC_DB_ROUTE = "/v1/services"


class SmokeCheckError(RuntimeError):
    pass


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _request_json(
    client: httpx.Client,
    *,
    path: str,
    check_name: str,
    params: dict[str, str] | None = None,
) -> object:
    try:
        response = client.get(path, params=params)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip()
        msg = f"{check_name} check failed with status {exc.response.status_code}"
        if detail:
            msg = f"{msg}: {detail}"
        raise SmokeCheckError(msg) from exc
    except httpx.HTTPError as exc:
        raise SmokeCheckError(f"{check_name} check failed: {exc}") from exc
    except JSONDecodeError as exc:
        raise SmokeCheckError(f"{check_name} check failed: response was not valid JSON") from exc


def run_smoke_checks(
    client: httpx.Client,
    *,
    wallet_address: str = DEFAULT_NONCE_ADDRESS,
    public_db_route: str = DEFAULT_PUBLIC_DB_ROUTE,
) -> list[str]:
    results: list[str] = []

    live_payload = _request_json(client, path="/health/live", check_name="live")
    if not isinstance(live_payload, dict) or live_payload.get("status") != "ok":
        msg = "live check failed: expected {'status': 'ok'}"
        raise SmokeCheckError(msg)
    results.append("live ok")

    ready_payload = _request_json(client, path="/health/ready", check_name="ready")
    if not isinstance(ready_payload, dict) or ready_payload.get("status") != "ok":
        msg = "ready check failed: expected {'status': 'ok'}"
        raise SmokeCheckError(msg)
    results.append("ready ok")

    nonce_payload = _request_json(
        client,
        path="/v1/auth/nonce",
        check_name="auth nonce",
        params={"address": wallet_address},
    )
    if not isinstance(nonce_payload, dict) or not isinstance(nonce_payload.get("nonce"), str):
        msg = "auth nonce check failed: expected a JSON object with a nonce string"
        raise SmokeCheckError(msg)
    if not nonce_payload["nonce"]:
        msg = "auth nonce check failed: nonce was empty"
        raise SmokeCheckError(msg)
    results.append("auth nonce ok")

    public_db_payload = _request_json(
        client,
        path=public_db_route,
        check_name="public db route",
    )
    if not isinstance(public_db_payload, list):
        msg = "public db route check failed: expected a JSON array"
        raise SmokeCheckError(msg)
    results.append(f"{public_db_route} ok")

    return results


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deployment smoke checks against a deployed API base URL.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SMOKE_BASE_URL"),
        help="Deployed API base URL. Can also be provided via SMOKE_BASE_URL.",
    )
    parser.add_argument(
        "--wallet-address",
        default=DEFAULT_NONCE_ADDRESS,
        help="Wallet address used for the auth nonce check.",
    )
    parser.add_argument(
        "--public-db-route",
        default=DEFAULT_PUBLIC_DB_ROUTE,
        help="DB-backed public route that should return a JSON list.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds.",
    )
    args = parser.parse_args(argv)
    if not args.base_url:
        parser.error("--base-url or SMOKE_BASE_URL is required")
    args.base_url = _normalize_base_url(args.base_url)
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
            results = run_smoke_checks(
                client,
                wallet_address=args.wallet_address,
                public_db_route=args.public_db_route,
            )
    except SmokeCheckError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    for result in results:
        sys.stdout.write(f"{result}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
