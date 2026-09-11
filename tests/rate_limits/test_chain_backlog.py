"""Real signed fills against a paused Localnet chain, including automatic recovery."""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
import pytest_asyncio

from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from tests.rate_limits import rl_config
from tests.rate_limits.rl_actions import create_resting_order, ensure_flat, resolve_market, wire
from tests.rate_limits.rl_errors import assert_no_retry_hint, assert_venue_verdict, capture_rest_reject
from tests.rate_limits.rl_hooks import require_hook, run_hook

pytestmark = [pytest.mark.rate_limits, rl_config.requires_rate_limits]


@pytest_asyncio.fixture(name="chain_clients", loop_scope="session")
async def _chain_clients():
    for action in ("prepare", "pause", "snapshot", "mine", "resume", "restore"):
        require_hook(f"chain_{action}")
    assert os.environ.get("CHAIN_ID") == "31337", "this fault test requires Localnet"
    assert urlsplit(os.environ["REYA_API_URL"]).hostname in ("127.0.0.1", "localhost", "::1")
    # Function-scoped REST clients leave no background WebSocket connections
    # that would consume the later per-IP connection-cap tests' budget.
    clients = [
        ReyaTradingClient(
            config=TradingConfig(
                api_url=os.environ["REYA_API_URL"],
                chain_id=31337,
                owner_wallet_address=os.environ[f"SPOT_WALLET_ADDRESS_{index}"],
                private_key=os.environ[f"SPOT_PRIVATE_KEY_{index}"],
                account_id=int(os.environ[f"SPOT_ACCOUNT_ID_{index}"]),
                orders_gateway_address=os.environ["REYA_ORDERS_GATEWAY"],
                dex_id_override=int(os.environ["REYA_DEX_ID"]) if os.environ.get("REYA_DEX_ID") else None,
            )
        )
        for index in (1, 2)
    ]
    try:
        for client in clients:
            await client.start()
        yield clients
    finally:
        await asyncio.gather(*(client.close() for client in clients))


async def test_chain_backlog_halts_creates_allows_cancel_and_drain_then_reopens(
    chain_clients, rl_suite_config, record_property
):
    seller, buyer = chain_clients
    market = await resolve_market(buyer, rl_suite_config.symbol)
    qty, price = market.min_qty, market.oracle_price
    wallet, account = buyer.config.owner_wallet_address, buyer.config.account_id
    assert account is not None and seller.config.account_id != account
    observations = {}

    async def control(action):
        return await run_hook(f"chain_{action}", wallet, account, timeout_s=360)

    async def snapshot():
        return json.loads(await control("snapshot"))

    async def wait_snapshot(label, predicate, timeout=30):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            state = await snapshot()
            observations[label] = state
            if predicate(state):
                return state
            assert asyncio.get_running_loop().time() < deadline, f"{label}: {state}"
            await asyncio.sleep(0.25)

    async def base_balance(client):
        rows = await client.get_account_balances()
        return sum(
            (
                Decimal(row.real_balance)
                for row in rows
                if row.account_id == client.config.account_id and row.asset == market.base_asset
            ),
            Decimal(0),
        )

    async def settled(units):
        deadline = asyncio.get_running_loop().time() + 30
        while True:
            actual = await asyncio.gather(base_balance(buyer), base_balance(seller))
            if tuple(actual) == (baseline[0] + qty * units, baseline[1] - qty * units):
                observations[f"settled_{units}_fills"] = list(map(str, actual))
                return
            assert asyncio.get_running_loop().time() < deadline, f"settlement delta missing: {actual}"
            await asyncio.sleep(0.25)

    async def order(client, is_buy, units, tif=TimeInForce.GTC):
        return await client.create_limit_order(
            LimitOrderParameters(
                symbol=market.symbol,
                is_buy=is_buy,
                qty=wire(qty * units),
                limit_px=wire(price),
                time_in_force=tif,
            )
        )

    async def cross():
        response = await order(buyer, True, 1, TimeInForce.IOC)
        assert Decimal(response.cum_qty) == qty, response
        return response

    async def assert_halted(label):
        reject = await capture_rest_reject(create_resting_order(buyer, market), label)
        assert reject.code == "TRADING_HALTED_ERROR", reject.describe()
        assert "settlement confirmation is lagging" in (reject.message or ""), reject.describe()
        assert_venue_verdict(reject, label)
        assert_no_retry_hint(reject, label)
        observations[label] = reject.describe()

    try:
        await control("prepare")
        await ensure_flat(buyer, rl_suite_config, market.symbol)
        await ensure_flat(seller, rl_suite_config, market.symbol)
        healthy = await wait_snapshot(
            "healthy",
            lambda s: all(
                s["metrics"][name] == 0
                for name in (
                    "me.settle.confirmed_nonce_lag",
                    "me.settle.trading_halted",
                    "me.broadcast.trading_halted",
                    "me.indexer.trading_halted",
                    "me.risk.in_flight.size",
                )
            ),
        )
        baseline = await asyncio.gather(base_balance(buyer), base_balance(seller))
        assert baseline[1] >= qty * 6, "seller needs real base collateral"
        maker = await order(seller, False, 6)
        assert Decimal(maker.cum_qty) == 0
        await cross()
        await settled(1)  # Prove this pair signs fills the actual contracts accept.
        cancel_one = await create_resting_order(buyer, market)
        await create_resting_order(buyer, market)
        await control("pause")
        paused = await snapshot()
        for _ in range(4):
            await cross()
        halted = await wait_snapshot("halted", lambda s: s["metrics"]["me.settle.trading_halted"] == 1)
        assert halted["block"] == paused["block"], "the local chain did not stay paused"
        assert halted["pending"], "no real settlement transaction reached Anvil"
        assert halted["metrics"]["me.settle.confirmed_nonce_lag"] >= 1
        assert halted["metrics"]["me.risk.in_flight.size"] >= 4
        assert halted["metrics"]["me.broadcast.trading_halted"] == 0
        assert halted["metrics"]["me.indexer.trading_halted"] == 0
        await assert_halted("backlog_reject")
        await buyer.cancel_order(order_id=cancel_one, symbol=market.symbol, account_id=account)
        cancelled = await buyer.mass_cancel(symbol=market.symbol, account_id=account)
        assert cancelled.cancelled_count == 1, cancelled
        await seller.cancel_order(order_id=maker.order_id, symbol=market.symbol, account_id=seller.config.account_id)

        # One block confirms the outstanding transaction. The broadcaster must
        # submit queued fills while admission remains halted and mining stays off.
        pending_hashes = {tx["hash"] for tx in halted["pending"]}
        await control("mine")
        draining = await wait_snapshot(
            "draining_while_halted",
            lambda s: (
                any(r["transactionHash"] in pending_hashes for r in s["receipts"])
                and any(tx["hash"] not in pending_hashes for tx in s["pending"])
            ),
            timeout=10,
        )
        assert draining["block"] == paused["block"] + 1
        assert draining["metrics"]["me.settle.trading_halted"] == 1
        assert all(int(r["status"], 16) == 1 for r in draining["receipts"])
        await assert_halted("draining_reject")

        await control("resume")
        recovered = await wait_snapshot(
            "recovered", lambda s: (not s["pending"] and all(value == 0 for value in s["metrics"].values()))
        )
        await settled(5)
        assert len(recovered["receipts"]) >= 2, "queued fills never became separate chain transactions"
        assert all(int(r["status"], 16) == 1 for r in recovered["receipts"])
        await order(seller, False, 1)
        await cross()  # Reopens automatically, without a control reset or restart.
        await settled(6)
        final = await snapshot()
        assert len(healthy["generation"]) == 1 and all(
            state["generation"] == healthy["generation"] for state in (halted, draining, recovered, final)
        ), "the engine restarted during the halt/recovery cycle"
    finally:
        record_property("chain_backlog_evidence", json.dumps(observations))
        try:
            await control("resume")
            await ensure_flat(buyer, rl_suite_config, market.symbol)
            await ensure_flat(seller, rl_suite_config, market.symbol)
        finally:
            await control("restore")
