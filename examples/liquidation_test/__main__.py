"""Read-only planning by default; --execute submits two bounded IOC orders."""

from typing import Any, TextIO

import argparse
import asyncio
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

from examples.liquidation_test.readiness import API, OWNER, Readiness, emit, signer_from_env
from examples.liquidation_test.sizing import decimal, positive, quote
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import MAINNET_ORDERS_GATEWAY, TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters


def fresh(timestamp: Any, max_age: int) -> None:
    age_ms = Decimal(str(time.time_ns() // 1_000_000)) - decimal(timestamp)
    if age_ms < -2000 or age_ms > max_age * 1000:
        raise ValueError("Market/depth data is stale or future-dated; no order prepared")


def build_plan(read: Readiness, args: argparse.Namespace, account: int, is_buy: bool) -> dict:
    read.assert_empty([account])
    margin = read.margin(account)
    balance = positive(margin["margin_balance"])
    if balance > args.max_collateral:
        raise ValueError("Account collateral exceeds the operator's test budget")
    if abs(balance - decimal(margin["real_balance"])) > Decimal("0.000002"):
        raise ValueError("Flat-account margin and real balance disagree")
    definitions = read.api("/perpMarketDefinitions")
    market = next((item for item in definitions if item["symbol"] == args.symbol), None)
    if market is None:
        raise ValueError("Requested perp market is not enabled")
    summary = read.api(f"/perpMarket/{args.symbol}/summary")
    if not summary.get("markPrice"):
        raise ValueError("Cutover markPrice feed is unavailable; legacy oracle/pool prices are not a substitute")
    fresh(summary["pricesUpdatedAt"], args.max_age)
    depth = read.api(f"/market/{args.symbol}/depth")
    fresh(depth["updatedAt"], args.max_age)
    fee_tiers = read.api("/feeTiers")
    # A conservative upper bound: do not assume a discount or a maker fill.
    fee = max(decimal(tier["takerFee"]) for tier in fee_tiers)
    result = quote(
        balance=balance,
        mark=positive(summary["markPrice"]),
        lmr_rate=positive(market["liquidationMarginParameter"]),
        fee_rate=fee,
        buffer=args.margin_buffer,
        slippage_bps=args.slippage_bps,
        tick=positive(market["tickSize"]),
        step=positive(market["qtyStepSize"]),
        minimum=positive(market["minOrderQty"]),
        levels=depth["asks" if is_buy else "bids"],
        is_buy=is_buy,
        max_notional=args.max_notional,
    )
    return {
        "account_id": account,
        "symbol": args.symbol,
        "market_id": market["marketId"],
        "margin_before": margin,
        "planned_at_ms": min(int(summary["pricesUpdatedAt"]), int(depth["updatedAt"])),
        **result,
    }


def journal(file: TextIO, event: str, **details: Any) -> None:
    file.write(json.dumps({"at_ms": time.time_ns() // 1_000_000, "event": event, **details}) + "\n")
    file.flush()
    os.fsync(file.fileno())


async def wait_settled(read: Readiness, plan: dict, timeout: int) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        margin = read.margin(plan["account_id"])
        positions = read.wallet("positions")
        expected_side = "B" if plan["side"] == "LONG" else "A"
        matched = any(
            position["accountId"] == plan["account_id"]
            and position["symbol"] == plan["symbol"]
            and position["side"] == expected_side
            and decimal(position["qty"]) == decimal(plan["qty"])
            for position in positions
        )
        if decimal(margin["lmr"]) > 0 and matched:
            return margin
        await asyncio.sleep(1)
    raise ValueError("Settlement was not confirmed; do not retry or send the other leg automatically")


async def execute_plans(read: Readiness, args: argparse.Namespace, key: str, plans: list[dict]) -> None:
    if not args.journal:
        raise ValueError("--execute requires a new --journal path")
    # Exclusive creation prevents rerunning an interrupted batch under the same
    # journal. Never write private keys, signed payloads or authorization headers.
    with Path(args.journal).open("x", encoding="utf-8") as file:
        journal(file, "started", owner=read.owner, plans=plans)
        for original in plans:
            # Fresh validation before EACH leg. The two orders are not atomic.
            plan = build_plan(read, args, original["account_id"], original["side"] == "LONG")
            config = TradingConfig(
                api_url=API,
                chain_id=1729,
                owner_wallet_address=read.owner,
                private_key=key,
                account_id=plan["account_id"],
                orders_gateway_address=MAINNET_ORDERS_GATEWAY,
                dex_id_override=2,
            )
            client_order_id = time.time_ns() // 1000
            async with ReyaTradingClient(config) as client:
                await client.start()
                fresh(plan["planned_at_ms"], args.max_age)
                journal(file, "submitting", plan=plan, client_order_id=str(client_order_id))
                # Exactly one attempt. A timeout may have submitted successfully.
                response = await client.create_limit_order(
                    LimitOrderParameters(
                        symbol=plan["symbol"],
                        is_buy=plan["side"] == "LONG",
                        limit_px=plan["limit_px"],
                        qty=plan["qty"],
                        time_in_force=TimeInForce.IOC,
                        reduce_only=False,
                        client_order_id=client_order_id,
                    )
                )
                status = response.status.value
                journal(
                    file,
                    "acknowledged",
                    account_id=plan["account_id"],
                    order_id=response.order_id,
                    status=status,
                    cum_qty=response.cum_qty,
                    exec_qty=response.exec_qty,
                )
                filled = decimal(response.cum_qty or response.exec_qty or "0")
                if status != "FILLED" or filled != decimal(plan["qty"]):
                    raise ValueError("Order rejected, cancelled or partially filled; inspect the journal and positions")
            margin = await wait_settled(read, plan, args.settlement_timeout)
            journal(file, "settled", margin=margin)
            emit({"settled_account": plan["account_id"], "margin": margin})
            if decimal(margin["liquidation_delta"]) <= 0:
                raise ValueError("First account is already at/below LMR; inspect liquidation before proceeding")
        journal(file, "completed")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--owner", default=OWNER)
    sub = root.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="Read account state and optional delegate identity/permission")
    inspect.add_argument("--env-file")
    inspect.add_argument("--key-var", default="PERP_PRIVATE_KEY_1")
    create = sub.add_parser("create", help="Print unsigned account-creation transactions for the owner wallet")
    create.add_argument("--count", type=int, choices=[1, 2], default=2)
    fund = sub.add_parser("fund", help="Simulate and print one unsigned transfer transaction funding both accounts")
    fund.add_argument("--source-account", type=int, required=True)
    fund.add_argument("--long-account", type=int, required=True)
    fund.add_argument("--short-account", type=int, required=True)
    fund.add_argument("--amount", type=positive, required=True, help="rUSD per destination")
    trade = sub.add_parser("open", help="Plan two opposite near-LMR IOC orders; no orders unless --execute")
    trade.add_argument("--long-account", type=int, required=True)
    trade.add_argument("--short-account", type=int, required=True)
    trade.add_argument("--symbol", required=True)
    trade.add_argument("--max-collateral", type=positive, required=True, help="rUSD budget cap per account")
    trade.add_argument("--max-notional", type=positive, required=True, help="USD notional cap per account")
    trade.add_argument("--margin-buffer", type=positive, required=True, help="Positive USD buffer above LMR at limit")
    trade.add_argument("--slippage-bps", type=decimal, required=True)
    trade.add_argument("--max-age", type=int, default=5)
    trade.add_argument("--settlement-timeout", type=int, default=60)
    trade.add_argument("--env-file")
    trade.add_argument("--key-var", default="PERP_PRIVATE_KEY_1")
    trade.add_argument("--journal")
    trade.add_argument("--execute", action="store_true", help="Sign and submit live mainnet orders")
    return root


async def main(args: argparse.Namespace) -> None:
    read = Readiness(args.owner)
    if args.command == "create":
        emit({"unsigned_transactions": read.create_plan(args.count)})
    elif args.command == "fund":
        emit(
            {
                "unsigned_transaction": read.funding_plan(
                    args.source_account,
                    [args.long_account, args.short_account],
                    args.amount,
                )
            }
        )
    elif args.command == "inspect":
        accounts = read.wallet("accounts")
        result = {
            "owner": read.owner,
            "accounts": accounts,
            "margins": [read.margin(item["accountId"]) for item in accounts if item["accountId"] < 10**10],
        }
        if args.env_file:
            _, address = signer_from_env(args.env_file, args.key_var, read.owner)
            result["delegate"] = {
                "address": address,
                "gateway_permission": read.gateway.functions.hasPermission(read.owner, address).call(),
                "gas_wei": str(read.web3.eth.get_balance(read.web3.to_checksum_address(address))),
                "core_permissions": {
                    str(item["accountId"]): [
                        {"user": user, "permissions": [p.hex() for p in permissions]}
                        for user, permissions in read.core.functions.getAccountPermissions(item["accountId"]).call()
                    ]
                    for item in accounts
                    if item["accountId"] < 10**10
                },
            }
        emit(result)
    else:
        if args.long_account == args.short_account:
            raise ValueError("Long and short accounts must be distinct")
        if args.max_age <= 0 or args.settlement_timeout <= 0:
            raise ValueError("Timeouts must be positive")
        plans = [build_plan(read, args, args.long_account, True), build_plan(read, args, args.short_account, False)]
        emit({"execute": args.execute, "owner": read.owner, "plans": plans})
        if args.execute:
            if not args.env_file:
                raise ValueError("--execute requires an explicit mainnet --env-file")
            key, address = signer_from_env(args.env_file, args.key_var, read.owner)
            if not read.gateway.functions.hasPermission(read.owner, address).call():
                raise ValueError("Signing key lacks OrdersGateway permission for the owner")
            await execute_plans(read, args, key, plans)


if __name__ == "__main__":
    try:
        asyncio.run(main(parser().parse_args()))
    except ValueError as error:
        print(f"Stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        # An SDK/transport exception can embed the signed request. Do not echo it.
        print(f"Stopped: {type(error).__name__}; inspect the journal/state before any retry", file=sys.stderr)
        raise SystemExit(1) from None
