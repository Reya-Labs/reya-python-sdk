"""M1 — decimal-string precision round-trip (no float coercion in the model path).

A price/quantity is an exact decimal all the way through the stack: the wire
carries a decimal *string* and the SDK Pydantic models keep it a ``str``. This
test proves the SDK never round-trips a px/qty through a binary float (which
would turn ``"0.617"`` into ``"0.6170000000000001"`` or ``"0.617"`` via a
non-shortest formatter, and would make ``0.1 + 0.2`` aggregate to
``"0.30000000000000004"``).

It asserts on TWO independent views of the same value:

* the **raw wire string** the recorder captured off the socket (pre-Pydantic,
  via the ``on_data`` hook), and
* the **parsed model** attribute (``Order.limit_px`` / ``Order.qty`` /
  ``Level.px`` / ``Level.qty``),

for BOTH the ``depth`` level and the ``orderChanges`` order, plus the REST
``openOrders`` / ``depth`` projections.

Precision note (softened vs. the audit's literal ``2500.123456789`` example):
the localnet spot market ``WETHRUSD`` enforces ``tickSize=0.01`` /
``qtyStepSize=0.001`` — the matching engine rejects any sub-tick / sub-step
value with HTTP 400, so a 9-decimal order is not placeable. The test therefore
uses the finest fractional px/qty the market admits (2 px decimals, 3 qty
decimals) at deliberately non-round values, and additionally exercises the
float-hostile **aggregation** path (``0.1 + 0.2`` must aggregate to exactly
``"0.3"``), which is the sharpest float-coercion probe available under the
tick/step constraint.
"""

from __future__ import annotations

from typing import Any, Optional

import logging
import os
from decimal import Decimal

import httpx
import pytest

from sdk.async_api.order import Order as AsyncOrder
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.market_config import SpotTestConfig
from tests.market_data.md_ws_recorder import MarketDataRecorder
from tests.market_data.poll import wait_until

logger = logging.getLogger("reya.integration_tests")

# Finest fractional values WETHRUSD (tick 0.01 / step 0.001) admits, chosen
# non-round so any reformatting is visible. Distinct prices per scenario so the
# depth levels never collide.
ROUNDTRIP_PX = "10.13"
ROUNDTRIP_QTY = "0.617"
AGG_PX = "11.24"
AGG_QTY_A = "0.1"
AGG_QTY_B = "0.2"
AGG_QTY_SUM = "0.3"  # NOT "0.30000000000000004": aggregation must be exact.


async def _fetch_json(url: str) -> Any:
    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


def _raw_order_entry(recorder: MarketDataRecorder, order_id: str) -> Optional[dict[str, Any]]:
    for entry in recorder.raw_order_entries():
        if entry.get("orderId") == order_id:
            return entry
    return None


def _latest_raw_bid_level(recorder: MarketDataRecorder, px: str) -> Optional[dict[str, Any]]:
    """Latest raw wire bid-level dict for ``px`` (highest frame index wins, since
    depth updates are absolute per-level statements)."""
    best: Optional[dict[str, Any]] = None
    best_index = -1
    for level, index in recorder.raw_depth_update_levels("bids"):
        if level.get("px") == px and index > best_index:
            best, best_index = level, index
    return best


def _typed_order_event(recorder: MarketDataRecorder, order_id: str) -> Optional[AsyncOrder]:
    matches = [o for o in recorder.order_change_events() if o.order_id == order_id]
    return matches[-1] if matches else None


@pytest.mark.localnet
@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_precision_roundtrip(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    logger.info("=" * 80)
    logger.info("M1 DECIMAL PRECISION ROUND-TRIP: %s", spot_config.symbol)
    logger.info("=" * 80)

    symbol = spot_config.symbol
    address = spot_tester.owner_wallet_address
    assert address is not None
    base_url = spot_tester.client.config.api_url
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")

    await spot_tester.orders.close_all(fail_if_none=False)

    recorder = MarketDataRecorder(
        ws_url, address=address, symbol=symbol, subscribe_order_changes=True, subscribe_depth=True
    )
    recorder.connect()
    try:
        assert await wait_until(
            lambda: recorder.order_changes_snapshot is not None and recorder.depth_snapshot_copy() is not None,
            timeout=15.0,
        ), "did not receive both subscribe snapshots"

        # ---- Scenario A: single-order exact round-trip -------------------
        params = OrderBuilder.from_config(spot_config).buy().price(ROUNDTRIP_PX).qty(ROUNDTRIP_QTY).gtc().build()
        order_id = await spot_tester.orders.create_limit(params)
        assert order_id, "create_limit returned no order_id"

        assert await wait_until(
            lambda: _typed_order_event(recorder, order_id) is not None
            and any(o.status.value == "OPEN" for o in recorder.order_change_events() if o.order_id == order_id),
            timeout=15.0,
        ), "orderChanges OPEN for the precision order never arrived"

        # orderChanges — parsed model keeps the exact decimal string (as a str).
        typed_order = _typed_order_event(recorder, order_id)
        assert typed_order is not None
        assert isinstance(typed_order.limit_px, str) and isinstance(typed_order.qty, str), "px/qty are not str"
        assert typed_order.limit_px == ROUNDTRIP_PX, f"parsed limitPx {typed_order.limit_px!r} != {ROUNDTRIP_PX!r}"
        assert typed_order.qty == ROUNDTRIP_QTY, f"parsed qty {typed_order.qty!r} != {ROUNDTRIP_QTY!r}"

        # orderChanges — raw wire string (pre-Pydantic).
        raw_order = _raw_order_entry(recorder, order_id)
        assert raw_order is not None, "recorder captured no raw orderChanges entry for the order"
        assert raw_order.get("limitPx") == ROUNDTRIP_PX, f"raw wire limitPx {raw_order.get('limitPx')!r}"
        assert raw_order.get("qty") == ROUNDTRIP_QTY, f"raw wire qty {raw_order.get('qty')!r}"
        logger.info("orderChanges px/qty exact on raw wire AND parsed model: %s / %s", ROUNDTRIP_PX, ROUNDTRIP_QTY)

        # depth — level shows the exact decimal on both views.
        assert await wait_until(
            lambda: _latest_raw_bid_level(recorder, ROUNDTRIP_PX) is not None, timeout=15.0
        ), "depth update for the precision bid never arrived"
        raw_level = _latest_raw_bid_level(recorder, ROUNDTRIP_PX)
        assert raw_level is not None
        assert raw_level.get("px") == ROUNDTRIP_PX, f"raw wire depth px {raw_level.get('px')!r}"
        assert raw_level.get("qty") == ROUNDTRIP_QTY, f"raw wire depth qty {raw_level.get('qty')!r}"

        typed_level = next(
            (lvl for frame in recorder.depth_updates() for lvl in frame.bids if lvl.px == ROUNDTRIP_PX),
            None,
        )
        assert typed_level is not None, "parsed depth level for the precision bid not found"
        assert isinstance(typed_level.px, str) and isinstance(typed_level.qty, str)
        assert typed_level.px == ROUNDTRIP_PX and typed_level.qty == ROUNDTRIP_QTY
        logger.info("depth px/qty exact on raw wire AND parsed model: %s / %s", ROUNDTRIP_PX, ROUNDTRIP_QTY)

        # REST projections carry the same exact decimal string.
        rest_open = await _fetch_json(f"{base_url.rstrip('/')}/wallet/{address}/openOrders")
        rest_orders = rest_open if isinstance(rest_open, list) else rest_open.get("data", [])
        rest_order = next((o for o in rest_orders if o.get("orderId") == order_id), None)
        assert rest_order is not None, "REST openOrders missing the precision order"
        assert (
            rest_order.get("limitPx") == ROUNDTRIP_PX and rest_order.get("qty") == ROUNDTRIP_QTY
        ), f"REST openOrders px/qty {rest_order.get('limitPx')!r}/{rest_order.get('qty')!r}"
        rest_depth = await _fetch_json(f"{base_url.rstrip('/')}/market/{symbol}/depth")
        rest_bid = next((b for b in (rest_depth.get("bids") or []) if b.get("px") == ROUNDTRIP_PX), None)
        assert rest_bid is not None and rest_bid.get("qty") == ROUNDTRIP_QTY, f"REST depth bid {rest_bid!r}"
        logger.info("REST openOrders + depth agree exactly: OK")

        # ---- Scenario B: float-hostile aggregation (0.1 + 0.2 == 0.3) ----
        agg_a = OrderBuilder.from_config(spot_config).buy().price(AGG_PX).qty(AGG_QTY_A).gtc().build()
        agg_b = OrderBuilder.from_config(spot_config).buy().price(AGG_PX).qty(AGG_QTY_B).gtc().build()
        id_a = await spot_tester.orders.create_limit(agg_a)
        id_b = await spot_tester.orders.create_limit(agg_b)
        assert id_a and id_b, "both aggregation orders must be created"

        async def _agg_level_settled() -> bool:
            level = _latest_raw_bid_level(recorder, AGG_PX)
            return level is not None and level.get("qty") == AGG_QTY_SUM

        assert await wait_until(_agg_level_settled, timeout=15.0), (
            "aggregated depth qty at "
            f"{AGG_PX} never settled to the exact {AGG_QTY_SUM!r} "
            f"(latest raw level: {_latest_raw_bid_level(recorder, AGG_PX)!r}) — float coercion in aggregation?"
        )
        agg_raw = _latest_raw_bid_level(recorder, AGG_PX)
        assert agg_raw is not None and agg_raw.get("qty") == AGG_QTY_SUM
        # A float sum of 0.1 + 0.2 would serialize as "0.30000000000000004".
        assert "0000000" not in str(agg_raw.get("qty")), f"aggregated qty looks float-derived: {agg_raw.get('qty')!r}"

        rest_depth2 = await _fetch_json(f"{base_url.rstrip('/')}/market/{symbol}/depth")
        rest_agg = next((b for b in (rest_depth2.get("bids") or []) if b.get("px") == AGG_PX), None)
        assert rest_agg is not None and rest_agg.get("qty") == AGG_QTY_SUM, f"REST aggregated qty {rest_agg!r}"
        assert Decimal(str(rest_agg.get("qty"))) == Decimal(AGG_QTY_SUM)
        logger.info(
            "M1 PASS: exact decimal round-trip on wire+model+REST; 0.1+0.2 aggregates to exactly %s", AGG_QTY_SUM
        )
    finally:
        recorder.close()
        await spot_tester.orders.close_all(fail_if_none=False)
