"""T2 — depth continuity + ``?limit``.

Subscribes to depth, captures the snapshot, then maintains a local book by
applying absolute per-level diffs (``qty == "0"`` removes a level) as orders are
placed and cancelled. Asserts:

* the locally-maintained book converges to REST ``/depth`` (no param = full book),
  proving snapshot + diffs are self-consistent and lossless, and
* ``/depth?limit=1`` returns exactly the top level per side of that same book.

The generated ``MarketDataApi.get_market_depth`` does not expose the ``limit``
query param yet, so the top-N slice is exercised with a direct HTTP GET against
the SDK's configured base URL (reported as an SDK regen gap, not worked around
in library code).
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal

import httpx
import pytest

from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.market_config import SpotTestConfig
from tests.market_data.local_book import LocalBook, depth_sides
from tests.market_data.md_ws_recorder import MarketDataRecorder
from tests.market_data.poll import wait_until

logger = logging.getLogger("reya.integration_tests")


def _build_local_book(recorder: MarketDataRecorder) -> LocalBook | None:
    snapshot = recorder.depth_snapshot_copy()
    if snapshot is None:
        return None
    book = LocalBook.from_snapshot(snapshot)
    for frame in recorder.depth_updates():
        book.apply(frame)
    return book


async def _fetch_depth_raw(base_url: str, symbol: str, limit: int | None = None) -> dict:
    url = f"{base_url.rstrip('/')}/market/{symbol}/depth"
    params = {"limit": str(limit)} if limit is not None else None
    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload: dict = response.json()
        return payload


@pytest.mark.localnet
@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_depth_continuity(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    logger.info("=" * 80)
    logger.info("T2 DEPTH CONTINUITY + LIMIT: %s", spot_config.symbol)
    logger.info("=" * 80)

    symbol = spot_config.symbol
    await spot_tester.orders.close_all(fail_if_none=False)

    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
    recorder = MarketDataRecorder(ws_url, symbol=symbol, subscribe_depth=True)
    recorder.connect()
    try:
        assert await wait_until(
            lambda: recorder.depth_snapshot_copy() is not None, timeout=10.0
        ), "did not receive depth subscribe snapshot"
        snap = recorder.depth_snapshot_copy()
        assert snap is not None
        logger.info("Depth snapshot: %d bids, %d asks", len(snap.bids), len(snap.asks))

        # Place two resting bids at distinct safe no-match prices, then remove one.
        base = int(spot_config.get_safe_no_match_buy_price())
        low_px, high_px = base, base + 2
        low_params = OrderBuilder.from_config(spot_config).buy().price(str(low_px)).gtc().build()
        high_params = OrderBuilder.from_config(spot_config).buy().price(str(high_px)).gtc().build()

        low_id = await spot_tester.orders.create_limit(low_params)
        high_id = await spot_tester.orders.create_limit(high_params)
        assert low_id and high_id, "both bid creates must return order ids"
        logger.info("Placed bids at %s and %s", low_px, high_px)

        # Local book (snapshot + diffs) should reflect both new levels.
        async def _both_bids_present() -> bool:
            book = _build_local_book(recorder)
            return book is not None and Decimal(low_px) in book.bids and Decimal(high_px) in book.bids

        assert await wait_until(_both_bids_present, timeout=15.0), "depth diffs did not add both bid levels"

        # Cancel the low bid -> expect an absolute qty-0 removal diff for that level.
        await spot_tester.client.cancel_order(order_id=low_id, symbol=symbol, account_id=spot_tester.account_id)

        async def _low_bid_removed() -> bool:
            book = _build_local_book(recorder)
            return book is not None and Decimal(low_px) not in book.bids and Decimal(high_px) in book.bids

        assert await wait_until(_low_bid_removed, timeout=15.0), "qty-0 diff did not remove the cancelled bid level"
        logger.info("qty-0 removal applied: low bid gone, high bid retained")

        # Convergence: local book (snapshot + absolute diffs) == REST full book.
        async def _converged() -> bool:
            book = _build_local_book(recorder)
            if book is None:
                return False
            rest = await spot_tester.data.market_depth(symbol)
            rest_bids, rest_asks = depth_sides(rest)
            return book.bids_sorted() == rest_bids and book.asks_sorted() == rest_asks

        assert await wait_until(_converged, timeout=20.0), "locally-maintained book did not converge to REST /depth"
        logger.info("Local book converged to REST full depth: OK")

        # ?limit=1 == top level per side of the same (full) book.
        base_url = spot_tester.client.config.api_url
        full = await spot_tester.data.market_depth(symbol)
        full_bids, full_asks = depth_sides(full)
        limited = await _fetch_depth_raw(base_url, symbol, limit=1)
        lim_bids = [(Decimal(b["px"]), Decimal(b["qty"])) for b in limited.get("bids", [])]
        lim_asks = [(Decimal(a["px"]), Decimal(a["qty"])) for a in limited.get("asks", [])]

        assert len(lim_bids) == min(1, len(full_bids)), f"limit=1 returned {len(lim_bids)} bids"
        assert len(lim_asks) == min(1, len(full_asks)), f"limit=1 returned {len(lim_asks)} asks"
        if full_bids:
            assert lim_bids[0] == full_bids[0], f"limit=1 bid {lim_bids[0]} != top bid {full_bids[0]}"
        if full_asks:
            assert lim_asks[0] == full_asks[0], f"limit=1 ask {lim_asks[0]} != top ask {full_asks[0]}"

        # No-param full book must be a superset of the top-1 slice.
        assert len(full_bids) >= len(lim_bids) and len(full_asks) >= len(lim_asks)
        logger.info("T2 PASS: convergence holds; limit=1 -> %d bid / %d ask top level(s)", len(lim_bids), len(lim_asks))
    finally:
        recorder.close()
        await spot_tester.orders.close_all(fail_if_none=False)


@pytest.mark.localnet
@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_depth_residual_qty_on_partial_cancel(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """S2a — cancelling ONE of two orders at the same price is an absolute qty
    REDUCTION, not a qty-0 removal: the price level survives with the remaining
    order's quantity, and the diff that changes it carries the reduced (positive)
    qty. This is the residual-qty aggregation contract T2's single-order removal
    can't exercise."""
    logger.info("=" * 80)
    logger.info("S2a DEPTH RESIDUAL-QTY (partial cancel keeps level): %s", spot_config.symbol)
    logger.info("=" * 80)

    symbol = spot_config.symbol
    await spot_tester.orders.close_all(fail_if_none=False)

    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
    recorder = MarketDataRecorder(ws_url, symbol=symbol, subscribe_depth=True)
    recorder.connect()
    try:
        assert await wait_until(
            lambda: recorder.depth_snapshot_copy() is not None, timeout=10.0
        ), "did not receive depth subscribe snapshot"

        px = "10.13"
        cancel_qty, keep_qty = "0.002", "0.003"
        aggregate = Decimal(cancel_qty) + Decimal(keep_qty)  # 0.005
        px_d = Decimal(px)

        cancel_params = OrderBuilder.from_config(spot_config).buy().price(px).qty(cancel_qty).gtc().build()
        keep_params = OrderBuilder.from_config(spot_config).buy().price(px).qty(keep_qty).gtc().build()
        cancel_id = await spot_tester.orders.create_limit(cancel_params)
        keep_id = await spot_tester.orders.create_limit(keep_params)
        assert cancel_id and keep_id, "both same-price bids must be created"

        # Level aggregates to cancel_qty + keep_qty.
        async def _aggregated() -> bool:
            book = _build_local_book(recorder)
            return book is not None and book.bids.get(px_d) == aggregate

        assert await wait_until(_aggregated, timeout=15.0), f"level {px} did not aggregate to {aggregate}"

        frames_before_cancel = len(recorder.depth_updates())
        await spot_tester.client.cancel_order(order_id=cancel_id, symbol=symbol, account_id=spot_tester.account_id)

        # After the cancel the level must survive at exactly keep_qty (a reduction).
        async def _reduced_not_removed() -> bool:
            book = _build_local_book(recorder)
            return book is not None and book.bids.get(px_d) == Decimal(keep_qty)

        if not await wait_until(_reduced_not_removed, timeout=15.0):
            settled_book = _build_local_book(recorder)
            current_qty = settled_book.bids.get(px_d) if settled_book else None
            raise AssertionError(f"level {px} did not settle to the residual {keep_qty}; current={current_qty}")

        # The post-cancel diffs for this level are an absolute reduction to keep_qty,
        # and NONE of them removed it (qty "0"): the level was reduced, never deleted.
        post_cancel = recorder.depth_updates()[frames_before_cancel:]
        seen_qtys = [lvl.qty for frame in post_cancel for lvl in frame.bids if Decimal(lvl.px) == px_d]
        assert seen_qtys, f"no post-cancel depth diff mentioned level {px}"
        assert keep_qty in seen_qtys, f"post-cancel diffs never stated the residual {keep_qty}: {seen_qtys}"
        assert "0" not in seen_qtys and Decimal("0") not in [
            Decimal(q) for q in seen_qtys
        ], f"a post-cancel diff removed level {px} (qty 0) instead of reducing it: {seen_qtys}"

        # REST agrees: the level is present with the residual qty.
        rest = await spot_tester.data.market_depth(symbol)
        rest_bids, _ = depth_sides(rest)
        rest_level = next((qty for lvl_px, qty in rest_bids if lvl_px == px_d), None)
        assert rest_level == Decimal(keep_qty), f"REST depth level {px} qty {rest_level} != residual {keep_qty}"
        logger.info("S2a PASS: partial cancel reduced level %s to %s (survived, not qty-0)", px, keep_qty)
    finally:
        recorder.close()
        await spot_tester.orders.close_all(fail_if_none=False)


@pytest.mark.localnet
@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_depth_limit_truncation_both_sides(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """S2b — ``?limit=1`` really truncates: with two bid levels and one ask level
    resting, the top-1 slice returns exactly the best bid (EXCLUDING the second)
    and the single ask, while the no-param book carries all of them."""
    logger.info("=" * 80)
    logger.info("S2b DEPTH ?limit=1 TRUNCATION (both sides): %s", spot_config.symbol)
    logger.info("=" * 80)

    symbol = spot_config.symbol
    await spot_tester.orders.close_all(fail_if_none=False)

    # Two bid levels (best = higher px) + one ask level (safe-no-match high sell).
    second_bid_px, best_bid_px = Decimal("10.13"), Decimal("11.17")
    ask_px = Decimal("399999.87")  # below SAFE_NO_MATCH_SELL_PRICE (400000), never crosses
    qty = "0.003"
    for px, side_is_buy in [(second_bid_px, True), (best_bid_px, True), (ask_px, False)]:
        params = OrderBuilder.from_config(spot_config).side(side_is_buy).price(str(px)).qty(qty).gtc().build()
        oid = await spot_tester.orders.create_limit(params)
        assert oid, f"create_limit returned no id for {'BUY' if side_is_buy else 'SELL'} {px}"

    async def _book_ready() -> bool:
        rest = await spot_tester.data.market_depth(symbol)
        rest_bids, rest_asks = depth_sides(rest)
        bid_pxs = {p for p, _ in rest_bids}
        ask_pxs = {p for p, _ in rest_asks}
        return {second_bid_px, best_bid_px}.issubset(bid_pxs) and ask_px in ask_pxs

    assert await wait_until(_book_ready, timeout=20.0), "resting book did not reach 2 bids + 1 ask"

    base_url = spot_tester.client.config.api_url
    full = await spot_tester.data.market_depth(symbol)
    full_bids, full_asks = depth_sides(full)

    limited = await _fetch_depth_raw(base_url, symbol, limit=1)
    lim_bids = [(Decimal(b["px"]), Decimal(b["qty"])) for b in limited.get("bids", [])]
    lim_asks = [(Decimal(a["px"]), Decimal(a["qty"])) for a in limited.get("asks", [])]

    # Exactly the best bid; the second-best is excluded.
    assert len(lim_bids) == 1, f"limit=1 returned {len(lim_bids)} bids: {lim_bids}"
    assert lim_bids[0][0] == best_bid_px, f"limit=1 top bid {lim_bids[0][0]} != best {best_bid_px}"
    assert all(px != second_bid_px for px, _ in lim_bids), "limit=1 leaked the second-best bid level"
    # The ask side is exercised too: exactly the single ask.
    assert len(lim_asks) == 1, f"limit=1 returned {len(lim_asks)} asks: {lim_asks}"
    assert lim_asks[0][0] == ask_px, f"limit=1 top ask {lim_asks[0][0]} != {ask_px}"

    # The no-param full book is a superset: it retains the excluded second bid.
    assert second_bid_px in {px for px, _ in full_bids}, "full book lost the second bid level"
    assert best_bid_px in {px for px, _ in full_bids} and ask_px in {px for px, _ in full_asks}
    logger.info(
        "S2b PASS: limit=1 -> best bid %s (excluded %s), ask %s; full book kept all",
        best_bid_px,
        second_bid_px,
        ask_px,
    )
    await spot_tester.orders.close_all(fail_if_none=False)
