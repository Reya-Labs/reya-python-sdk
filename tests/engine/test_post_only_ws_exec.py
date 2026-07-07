"""
Post-only over ws-exec — live e2e, parametrized [spot, perp].

Engine-side post-only semantics (would-cross/touch matrices, one-tick
placement, resting-maker fills) are proven transport-independently via REST in
tests/engine/test_post_only_rejection.py (also [spot, perp]). This module pins
the ws-exec TRANSPORT's OWN risk surface for the feature, over BOTH markets via
the shared ``ws_exec_market`` fixture (the ws-exec client reuses REST's
``build_*_payload`` signing path verbatim, so the two transports put
byte-identical signed envelopes on the wire):

* request payload mapping + flag fidelity — postOnly=True survives
  WsCreateOrderRequest serialization, reaches the REAL engine, and the order
  rests with the flag set. The WS CreateOrderResponse model carries no
  postOnly echo (status/execQty/cumQty/orderId/clientOrderId only), so flag
  fidelity is asserted via REST openOrders read-back;
* business-rejection envelope mapping — POST_ONLY_WOULD_CROSS_ERROR surfaces
  as WsExecOperationError with the structured code, NO order is created, and
  the counterparty's resting ask is untouched (spot-pinned: see the test);
* validation envelope, both halves — post_only + IOC raises locally in the
  SHARED payload builder before any frame is sent (shared-builder reuse pin),
  and a raw correctly-signed frame that bypasses the guard is rejected
  server-side with INPUT_VALIDATION_ERROR through the per-op error envelope.

WS exec URL/credential gaps fail by default (see ``ws_exec_market``). The raw
negative probe reuses the raw-WebSocket helpers from tests/helpers/ws_exec_harness.py.
"""

from __future__ import annotations

from typing import cast

import asyncio
import os
import time
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from dotenv import load_dotenv

from sdk.async_exec_api.order_status import OrderStatus
from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.depth import Depth
from sdk.open_api.models.order import Order
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.auth.signatures import OrderTypeInt, TimeInForceInt
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from sdk.reya_ws_exec import ReyaWsExecClient, WsExecOperationError
from tests.helpers.liquidity_detector import SAFE_NO_MATCH_SELL_PRICE, skip_if_external_liquidity
from tests.helpers.reya_tester.data import DataOperations
from tests.helpers.ws_exec_harness import assert_per_op_error, raw_connect, raw_recv_until, raw_send_envelope
from tests.helpers.ws_exec_market import WsExecMarket
from tests.helpers.ws_exec_prerequisites import missing_env_vars, ws_exec_account_env_vars, ws_exec_prerequisite_missing

load_dotenv()

pytestmark = [pytest.mark.e2e, pytest.mark.post_only]

# Spot symbol + engineered prices for the spot-pinned would-cross probe (the
# transport-parity tests below take their symbol/price from ``ws_exec_market``).
SPOT_SYMBOL = "WETHRUSD"
# Engineered touch for the would-cross probe: counterparty ask and post-only
# buy meet at the safe no-match sell price. A rejection can never take
# liquidity, and any external ask below it would only make the probe MORE
# crossing (same error code), so the touch is deterministic. (The IOC probes
# below price at the market's passive m.rest_buy_px since they never reach the
# book.)
WOULD_CROSS_PX = str(SAFE_NO_MATCH_SELL_PRICE)

# The would-cross counterparty needs a second SPOT account to rest the
# engineered ask; missing SPOT_*_2 fails loudly by default.
_PROBER_ENV = ws_exec_account_env_vars("SPOT", 1)
_COUNTERPARTY_ENV = ws_exec_account_env_vars("SPOT", 2)
_WOULD_CROSS_ENV = ("REYA_WS_EXEC_URL",) + _PROBER_ENV + _COUNTERPARTY_ENV


def _preflight_would_cross_prerequisites() -> None:
    missing = missing_env_vars(_WOULD_CROSS_ENV)
    if missing:
        ws_exec_prerequisite_missing(
            "ws-exec post-only would-cross spot probe needs REYA_WS_EXEC_URL, Spot Account 1, and Spot Account 2",
            missing_env=missing,
        )


async def _wait_for_open_order(rest: ReyaTradingClient, order_id: str, timeout_s: float = 10.0) -> Order:
    """Poll openOrders until `order_id` appears — the OrdersProvider cache
    consumes the ME's Redis stream asynchronously w.r.t. the ws-exec ack."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        open_orders = await rest.get_open_orders()
        order = next((o for o in open_orders if o.order_id == order_id), None)
        if order is not None:
            return order
        await asyncio.sleep(0.2)
    raise AssertionError(f"Order {order_id} not visible via REST within {timeout_s}s")


async def _open_order_ids(rest: ReyaTradingClient) -> set[str]:
    """Snapshot of the account's open order ids — for asserting that a
    rejected post-only probe left openOrders unchanged."""
    return {str(order.order_id) for order in await rest.get_open_orders()}


async def _mark_price(rest: ReyaTradingClient, symbol: str, max_attempts: int = 5) -> float:
    """Mark price for the liquidity gate, retrying through the transient
    NO_PRICES_FOUND_FOR_SYMBOL_ERROR feed gap (mirrors DataOperations.current_price)."""
    last_exc: Exception | None = None
    for _ in range(max_attempts):
        try:
            return float(await rest.get_market_mark_price(symbol))
        except (ApiException, RuntimeError) as exc:
            error_text = str(exc)
            if (
                "NO_PRICES_FOUND_FOR_SYMBOL_ERROR" not in error_text
                and "Price not found" not in error_text
                and "did not include markPrice" not in error_text
            ):
                raise
            last_exc = exc
        await asyncio.sleep(0.3)
    raise RuntimeError(f"Mark price for {symbol} unavailable after {max_attempts} attempts") from last_exc


class _RestDepthOps:
    """Duck-typed stand-in for DataOperations: exposes the one method the
    shared liquidity gate calls (`market_depth`) over a bare REST client, so
    this module stays off the full ReyaTester fixture stack like the other
    ws-exec suites."""

    def __init__(self, rest: ReyaTradingClient) -> None:
        self._rest = rest

    async def market_depth(self, symbol: str) -> Depth:
        return await self._rest.markets.get_market_depth(symbol=symbol)


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def counterparty_rest():
    """A started REST client for SPOT account 2 — rests the engineered
    counterparty ask for the would-cross probe."""
    _preflight_would_cross_prerequisites()
    rest2 = ReyaTradingClient(TradingConfig.from_env_spot(account_number=2))
    await rest2.start()
    try:
        yield rest2
    finally:
        await rest2.close()


async def test_ws_exec_post_only_rests_and_reads_back(ws_exec_market: WsExecMarket):
    """Transport concern: request payload mapping + flag fidelity. A
    postOnly=True createOrder over ws-exec reaches the real engine and rests
    OPEN; the flag round-trips to the resting order via REST openOrders
    read-back (WsCreateOrderResponse carries no postOnly echo, so REST is the
    only fidelity oracle)."""
    m = ws_exec_market

    create = await m.ws.create_limit_order(
        LimitOrderParameters(
            symbol=m.symbol,
            is_buy=True,
            limit_px=m.rest_buy_px,
            qty=m.min_qty,
            time_in_force=TimeInForce.GTC,
            post_only=True,
        )
    )
    order_id = create.order_id
    assert order_id is not None, f"[{m.market_type}] post-only createOrder OK but missing orderId: {create}"
    assert (
        create.status == OrderStatus.OPEN
    ), f"[{m.market_type}] post-only GTC away from the touch must rest OPEN: {create.status}"

    try:
        order = await _wait_for_open_order(m.rest, order_id)
        assert order.post_only is True, f"[{m.market_type}] postOnly must read back True via REST: {order.post_only}"
        print(f"  [ws-exec {m.market_type}] post-only rested OPEN with postOnly=True orderId={order_id}")
    finally:
        await m.ws.cancel_order(order_id=order_id, symbol=m.symbol, account_id=m.rest.config.account_id)


async def test_ws_exec_post_only_would_cross_error_envelope(  # pylint: disable=redefined-outer-name
    counterparty_rest,
):
    """Transport concern: business-rejection envelope mapping. A post-only buy
    at EXACTLY the counterparty's best ask (touch counts as a cross) is
    rejected by the engine and ws-exec maps it to WsExecOperationError with
    code POST_ONLY_WOULD_CROSS_ERROR; NO order is created (openOrders
    unchanged) and the counterparty's resting ask is untouched.

    Spot-pinned: this needs an engineered CROSSING price + a same-market
    counterparty ask resting at it. The deterministic safe-no-match sell price
    ($400k) only rests cleanly on the ETH-priced spot book; on perp it sits
    far outside the IOC execution band / OrdersGateway-conditional mechanics
    and would not reliably engineer a touch. The engine post-only-would-cross
    semantics are already proven on BOTH markets via REST
    (test_post_only_rejection.py); this pins only the ws-exec error-envelope
    mapping, which is transport-uniform across markets.
    """
    _preflight_would_cross_prerequisites()
    config = TradingConfig.from_env_spot(account_number=1)
    rest = ReyaTradingClient(config)
    await rest.start()
    ws: ReyaWsExecClient | None = None
    try:
        markets = {mkt.symbol: mkt for mkt in await rest.reference.get_spot_market_definitions()}
        if SPOT_SYMBOL not in markets:
            ws_exec_prerequisite_missing(f"{SPOT_SYMBOL} not found in /spotMarketDefinitions")
        min_qty = str(markets[SPOT_SYMBOL].min_order_qty)
        oracle_symbol = f"{markets[SPOT_SYMBOL].base_asset}RUSDPERP"
        ws = ReyaWsExecClient(rest_client=rest, ws_url=os.environ["REYA_WS_EXEC_URL"])
        await ws.connect()

        mark_price = await _mark_price(rest, oracle_symbol)
        await skip_if_external_liquidity(
            cast(DataOperations, _RestDepthOps(rest)),
            SPOT_SYMBOL,
            mark_price,
            reason_prefix="post-only would-cross (ws-exec)",
        )

        before_ids = await _open_order_ids(rest)
        ask = await counterparty_rest.create_limit_order(
            LimitOrderParameters(
                symbol=SPOT_SYMBOL,
                is_buy=False,
                limit_px=WOULD_CROSS_PX,
                qty=min_qty,
                time_in_force=TimeInForce.GTC,
            )
        )
        ask_order_id = ask.order_id
        assert ask_order_id is not None, "counterparty GTC creation must return an order_id"

        try:
            await _wait_for_open_order(counterparty_rest, ask_order_id)

            with pytest.raises(WsExecOperationError) as exc_info:
                await ws.create_limit_order(
                    LimitOrderParameters(
                        symbol=SPOT_SYMBOL,
                        is_buy=True,
                        limit_px=WOULD_CROSS_PX,
                        qty=min_qty,
                        time_in_force=TimeInForce.GTC,
                        post_only=True,
                    )
                )
            assert (
                exc_info.value.code == "POST_ONLY_WOULD_CROSS_ERROR"
            ), f"Expected POST_ONLY_WOULD_CROSS_ERROR, got {exc_info.value.code}"
            print(f"  [ws-exec spot] post-only touch @ {WOULD_CROSS_PX} rejected OK code={exc_info.value.code}")

            after_ids = await _open_order_ids(rest)
            assert after_ids == before_ids, f"Rejected post-only must not create an order: {after_ids - before_ids}"
            untouched = next(
                (o for o in await counterparty_rest.get_open_orders() if o.order_id == ask_order_id),
                None,
            )
            assert untouched is not None, "Book must be untouched: the counterparty ask was consumed or cancelled"
            assert untouched.limit_px is not None and Decimal(untouched.limit_px) == Decimal(WOULD_CROSS_PX)
            assert untouched.qty is not None and Decimal(untouched.qty) == Decimal(min_qty)
            print("  [ws-exec spot] openOrders unchanged for the prober; counterparty ask untouched")
        finally:
            # If a regression let the probe rest, sweep it before the run ends.
            for leaked_id in (await _open_order_ids(rest)) - before_ids:
                await ws.cancel_order(order_id=leaked_id, symbol=SPOT_SYMBOL, account_id=rest.config.account_id)
            try:
                await counterparty_rest.cancel_order(
                    order_id=ask_order_id, symbol=SPOT_SYMBOL, account_id=counterparty_rest.config.account_id
                )
            except ApiException:  # nosec B110
                pass  # ask already gone (e.g. consumed by a regression) — the assertions above report it
    finally:
        if ws is not None:
            await ws.close()
        await rest.close()


async def test_ws_exec_post_only_ioc_rejected_by_client_guard(ws_exec_market: WsExecMarket):
    """Transport concern: the shared-builder client guard fires on the WS
    path. ReyaWsExecClient.create_limit_order delegates to the SAME
    build_create_limit_order_payload as REST, so post_only + IOC raises
    locally BEFORE any frame is sent (a regression to a WS-local payload
    builder would break this). Nothing reaches the wire or the book."""
    m = ws_exec_market

    before_ids = await _open_order_ids(m.rest)
    with pytest.raises(ValueError, match="post_only is not supported on IOC orders"):
        await m.ws.create_limit_order(
            LimitOrderParameters(
                symbol=m.symbol,
                is_buy=True,
                limit_px=m.rest_buy_px,
                qty=m.min_qty,
                time_in_force=TimeInForce.IOC,
                post_only=True,
            )
        )
    print(f"  [ws-exec {m.market_type}] client guard refused post_only + IOC locally")

    assert await _open_order_ids(m.rest) == before_ids, "Locally-guarded request must not change openOrders"


async def test_ws_exec_post_only_ioc_raw_rejected_envelope(ws_exec_market: WsExecMarket):
    """Transport concern: server-side validation envelope. Bypassing the SDK
    guard with a raw post_only=True + IOC createOrder frame — signed over the
    EXACT wire values so signature recovery passes and the post-only/IOC rule
    is what rejects — comes back as a per-op error envelope with
    INPUT_VALIDATION_ERROR. The safety property holds independently of the
    client guard (mirrors REST test_post_only_ioc_raw_rejected_by_api)."""
    m = ws_exec_market
    rest = m.rest
    assert rest.config.account_id is not None

    nonce = rest.get_next_nonce()
    deadline = int(time.time()) + 60
    signature = rest.signature_generator.sign_order(
        account_id=rest.config.account_id,
        market_id=rest.get_market_id_from_symbol(m.symbol),
        exchange_id=rest.config.dex_id,
        order_type=int(OrderTypeInt.LIMIT),
        is_buy=True,
        qty=Decimal(m.min_qty),
        limit_price=Decimal(m.rest_buy_px),
        trigger_price=Decimal(0),
        time_in_force=int(TimeInForceInt.IOC),
        client_order_id=0,
        reduce_only=False,
        expires_after=0,
        nonce=nonce,
        deadline=deadline,
        post_only=True,
    )
    payload = {
        "accountId": rest.config.account_id,
        "symbol": m.symbol,
        "exchangeId": rest.config.dex_id,
        "isBuy": True,
        "limitPx": m.rest_buy_px,
        "qty": m.min_qty,
        "orderType": "LIMIT",
        "timeInForce": "IOC",
        "postOnly": True,
        "expiresAfter": 0,
        "signature": signature,
        "nonce": str(nonce),
        "signerWallet": rest.signer_wallet_address,
        "deadline": deadline,
    }

    before_ids = await _open_order_ids(rest)
    raw_ws = raw_connect(os.environ["REYA_WS_EXEC_URL"])
    try:
        env_id = uuid.uuid4().hex[:12]
        raw_send_envelope(raw_ws, "createOrder", env_id, payload)
        resp = raw_recv_until(raw_ws, lambda f: f.get("id") == env_id and "ok" in f)
        err = assert_per_op_error(resp, ("INPUT_VALIDATION_ERROR",), "post_only+IOC raw createOrder")
        print(f"  [ws-exec {m.market_type}] raw post_only+IOC rejected OK code={err.get('error')!r}")
    finally:
        raw_ws.close()

    assert await _open_order_ids(rest) == before_ids, "Rejected post_only+IOC must not change openOrders"
