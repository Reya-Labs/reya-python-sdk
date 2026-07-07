#!/usr/bin/env python3
"""Reya Localnet frontend fixture helper.

This script prepares a deterministic ETH perp book through the same signed
REST/ws-exec order-entry paths exercised by the SDK E2E suite. It is intentionally
small and Localnet-only: missing accounts, keys, API, or ws-exec endpoints
are hard failures, not skipped coverage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv

from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from sdk.reya_ws_exec import ReyaWsExecClient


DEFAULT_SYMBOL = "ETHRUSDPERP"
DEFAULT_QTY = "0.001"
DEFAULT_BID_PX = "2400"
DEFAULT_ASK_PX = "2600"
DEFAULT_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class AccountSpec:
    account_id: int
    private_key: str
    wallet_address: str


def _die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        _die(f"Missing required environment variable: {name}")
    return value


def _require_loopback_url(name: str, *, allowed_schemes: set[str]) -> str:
    value = _env(name)
    parsed = urlparse(value)
    if parsed.scheme not in allowed_schemes or parsed.hostname not in {"127.0.0.1", "localhost"}:
        _die(f"{name} must point at the local Reya Localnet stack, got: {value}")
    return value


def _require_localnet_env() -> None:
    chain_id = _env("CHAIN_ID")
    if chain_id != "31337":
        _die(f"CHAIN_ID must be 31337 for Localnet fixture helper, got: {chain_id}")

    _require_loopback_url("REYA_API_URL", allowed_schemes={"http", "https"})
    _require_loopback_url("REYA_WS_EXEC_URL", allowed_schemes={"ws", "wss"})
    _env("REYA_ORDERS_GATEWAY")


def _account(number: int) -> AccountSpec:
    return AccountSpec(
        account_id=int(_env(f"PERP_ACCOUNT_ID_{number}")),
        private_key=_env(f"PERP_PRIVATE_KEY_{number}"),
        wallet_address=_env(f"PERP_WALLET_ADDRESS_{number}"),
    )


def _config(account: AccountSpec) -> TradingConfig:
    chain_id = int(_env("CHAIN_ID"))
    api_url = _env("REYA_API_URL")
    dex_id_env = os.environ.get("REYA_DEX_ID")
    return TradingConfig(
        account_id=account.account_id,
        api_url=api_url,
        chain_id=chain_id,
        dex_id_override=int(dex_id_env) if dex_id_env else None,
        orders_gateway_address=os.environ.get("REYA_ORDERS_GATEWAY"),
        owner_wallet_address=account.wallet_address,
        private_key=account.private_key,
    )


def _jsonable_model(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(by_alias=True, exclude_none=True, mode="json")
    if hasattr(model, "to_dict"):
        return model.to_dict()
    raise TypeError(f"Unsupported response model: {type(model)!r}")


async def _started_client(account: AccountSpec) -> ReyaTradingClient:
    client = ReyaTradingClient(_config(account))
    await client.start()
    return client


async def _mass_cancel(rest: ReyaTradingClient, symbol: str, transport: str) -> dict[str, Any]:
    if transport in {"ws-exec", "auto"} and os.environ.get("REYA_WS_EXEC_URL"):
        async with ReyaWsExecClient(rest_client=rest, ws_url=os.environ["REYA_WS_EXEC_URL"]) as ws:
            response = await ws.mass_cancel(symbol=symbol, account_id=rest.config.account_id)
        return {"transport": "ws-exec", "response": _jsonable_model(response)}

    if transport == "ws-exec":
        _die("REYA_WS_EXEC_URL is required when --transport ws-exec is selected")

    response = await rest.mass_cancel(symbol=symbol, account_id=rest.config.account_id)
    return {"transport": "rest", "response": _jsonable_model(response)}


async def _create_limit(
    rest: ReyaTradingClient,
    symbol: str,
    *,
    is_buy: bool,
    limit_px: str,
    qty: str,
    transport: str,
) -> dict[str, Any]:
    params = LimitOrderParameters(
        symbol=symbol,
        is_buy=is_buy,
        limit_px=limit_px,
        qty=qty,
        time_in_force=TimeInForce.GTC,
        post_only=True,
    )

    if transport in {"ws-exec", "auto"} and os.environ.get("REYA_WS_EXEC_URL"):
        async with ReyaWsExecClient(rest_client=rest, ws_url=os.environ["REYA_WS_EXEC_URL"]) as ws:
            response = await ws.create_limit_order(params)
        selected_transport = "ws-exec"
    elif transport == "ws-exec":
        _die("REYA_WS_EXEC_URL is required when --transport ws-exec is selected")
    else:
        response = await rest.create_limit_order(params)
        selected_transport = "rest"

    status_value = getattr(response.status, "value", response.status)
    if status_value != "OPEN":
        raise RuntimeError(f"Controlled-book order did not rest OPEN: {response}")

    return {
        "accountId": rest.config.account_id,
        "isBuy": is_buy,
        "limitPx": limit_px,
        "orderId": response.order_id,
        "qty": qty,
        "response": _jsonable_model(response),
        "transport": selected_transport,
    }


async def _wait_for_depth(rest: ReyaTradingClient, symbol: str, bid_px: str, ask_px: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    last_depth: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        depth = await rest.markets.get_market_depth(symbol=symbol)
        last_depth = _jsonable_model(depth)
        bid_found = any(Decimal(level.px) == Decimal(bid_px) for level in depth.bids)
        ask_found = any(Decimal(level.px) == Decimal(ask_px) for level in depth.asks)
        if bid_found and ask_found:
            return last_depth
        await asyncio.sleep(0.5)

    raise RuntimeError(
        f"Timed out waiting for controlled book depth bid={bid_px} ask={ask_px}; last_depth={last_depth}"
    )


async def _symbol_open_orders(rest: ReyaTradingClient, symbol: str) -> list[dict[str, Any]]:
    return [
        _jsonable_model(order)
        for order in await rest.get_open_orders()
        if order.symbol == symbol
    ]


async def _wait_for_no_open_orders(
    rest1: ReyaTradingClient,
    rest2: ReyaTradingClient,
    symbol: str,
    timeout_s: float,
) -> dict[str, list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout_s
    open_orders: dict[str, list[dict[str, Any]]] = {"1": [], "2": []}
    while time.monotonic() < deadline:
        open_orders = {
            "1": await _symbol_open_orders(rest1, symbol),
            "2": await _symbol_open_orders(rest2, symbol),
        }
        if not open_orders["1"] and not open_orders["2"]:
            return open_orders
        await asyncio.sleep(0.5)

    raise RuntimeError(
        f"Timed out waiting for controlled-book cleanup; remaining open orders: {open_orders}"
    )


async def _cleanup(symbol: str, *, transport: str) -> dict[str, Any]:
    account1 = _account(1)
    account2 = _account(2)
    rest1 = await _started_client(account1)
    rest2 = await _started_client(account2)
    try:
        cancels = [
            await _mass_cancel(rest1, symbol=symbol, transport=transport),
            await _mass_cancel(rest2, symbol=symbol, transport=transport),
        ]
        open_orders = await _wait_for_no_open_orders(
            rest1,
            rest2,
            symbol,
            DEFAULT_TIMEOUT_S,
        )
        return {"action": "cleanup", "cancels": cancels, "openOrders": open_orders, "symbol": symbol}
    finally:
        await rest1.close()
        await rest2.close()


async def _setup(
    symbol: str,
    *,
    bid_px: str,
    ask_px: str,
    qty: str,
    timeout_s: float,
    transport: str,
) -> dict[str, Any]:
    account1 = _account(1)
    account2 = _account(2)
    rest1 = await _started_client(account1)
    rest2 = await _started_client(account2)
    try:
        try:
            cleanup = await _cleanup(symbol, transport=transport)
            bid = await _create_limit(
                rest1,
                symbol,
                is_buy=True,
                limit_px=bid_px,
                qty=qty,
                transport=transport,
            )
            ask = await _create_limit(
                rest2,
                symbol,
                is_buy=False,
                limit_px=ask_px,
                qty=qty,
                transport=transport,
            )
            depth = await _wait_for_depth(rest1, symbol, bid_px, ask_px, timeout_s)
        except Exception:
            await _mass_cancel(rest1, symbol=symbol, transport=transport)
            await _mass_cancel(rest2, symbol=symbol, transport=transport)
            await _wait_for_no_open_orders(rest1, rest2, symbol, DEFAULT_TIMEOUT_S)
            raise

        return {
            "action": "setup",
            "cleanup": cleanup,
            "depth": {
                "asks": depth.get("asks", [])[:5],
                "bids": depth.get("bids", [])[:5],
                "updatedAt": depth.get("updatedAt"),
            },
            "orders": [bid, ask],
            "symbol": symbol,
        }
    finally:
        await rest1.close()
        await rest2.close()


async def _main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "cleanup"))
    parser.add_argument("--ask-px", default=DEFAULT_ASK_PX)
    parser.add_argument("--bid-px", default=DEFAULT_BID_PX)
    parser.add_argument("--qty", default=DEFAULT_QTY)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeout-s", default=DEFAULT_TIMEOUT_S, type=float)
    parser.add_argument(
        "--transport",
        choices=("auto", "rest", "ws-exec"),
        default="auto",
        help="Order-entry transport for setup and cleanup; auto uses ws-exec when REYA_WS_EXEC_URL is set.",
    )
    args = parser.parse_args()
    _require_localnet_env()

    if args.action == "cleanup":
        result = await _cleanup(args.symbol, transport=args.transport)
    else:
        result = await _setup(
            args.symbol,
            ask_px=args.ask_px,
            bid_px=args.bid_px,
            qty=args.qty,
            timeout_s=args.timeout_s,
            transport=args.transport,
        )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
