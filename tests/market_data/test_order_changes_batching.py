"""T1 — orderChanges batch shape.

Bursts N rapid create+cancel pairs on one wallet and asserts the orderChanges
WS stream is lossless and correctly ordered *regardless of frame batching*:
one ``channel_data`` frame may carry several entries in ``data[]`` (grouped per
ingest drain), so the test tolerates any frame size but requires that

* every one of the 2N lifecycle events (N opens, N cancels) is delivered,
* ``sequenceNumber`` is strictly increasing across the flattened arrival order
  (ordering preserved *within and across* frames), and
* the final WS-derived open set equals REST ``openOrders``.
"""

import asyncio
import logging
import os

import pytest

from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.market_config import SpotTestConfig
from tests.market_data.md_ws_recorder import MarketDataRecorder
from tests.market_data.poll import wait_until

logger = logging.getLogger("reya.integration_tests")

BURST_N = 5


def _status(order) -> str:
    status = order.status
    return status.value if hasattr(status, "value") else str(status)


@pytest.mark.localnet
@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_order_changes_batching(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    logger.info("=" * 80)
    logger.info("T1 ORDER CHANGES BATCHING: %s", spot_config.symbol)
    logger.info("=" * 80)

    address = spot_tester.owner_wallet_address
    assert address is not None, "spot tester must have an owner wallet address"

    # Start from a clean book so the pre-burst snapshot is unambiguous.
    await spot_tester.orders.close_all(fail_if_none=False)
    await asyncio.sleep(0.3)

    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
    recorder = MarketDataRecorder(ws_url, address=address, subscribe_order_changes=True)
    recorder.connect()
    try:
        got_snapshot = await wait_until(lambda: recorder.order_changes_snapshot is not None, timeout=10.0)
        assert got_snapshot, "did not receive orderChanges subscribe snapshot"
        logger.info(
            "Subscribed; snapshot has %d resting order(s)",
            len(recorder.order_changes_snapshot_orders()),
        )

        # --- burst N creates (distinct safe no-match bid prices, all rest) ---
        base = int(spot_config.get_safe_no_match_buy_price())
        prices = [base + i for i in range(BURST_N)]
        created_ids: list[str] = []
        for price in prices:
            params = OrderBuilder.from_config(spot_config).buy().price(str(price)).gtc().build()
            order_id = await spot_tester.orders.create_limit(params)
            assert order_id is not None, "create_limit returned no order_id"
            created_ids.append(order_id)
        created = set(created_ids)
        logger.info("Created %d orders: %s", len(created_ids), created_ids)

        def _saw_open_for_all() -> bool:
            seen = {o.order_id for o in recorder.order_change_events() if _status(o) == "OPEN"}
            return created.issubset(seen)

        assert await wait_until(_saw_open_for_all, timeout=15.0), (
            "orderChanges did not deliver an OPEN for every created order; "
            f"saw {sorted({o.order_id for o in recorder.order_change_events()})}"
        )

        # WS-derived open set == REST openOrders at the peak.
        rest_open = {o.order_id for o in await spot_tester.client.get_open_orders() if o.symbol == spot_config.symbol}
        assert created.issubset(rest_open), f"REST openOrders missing some created: {created - rest_open}"

        # --- burst N cancels ---
        for order_id in created_ids:
            await spot_tester.client.cancel_order(
                order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
            )

        def _saw_cancel_for_all() -> bool:
            seen = {o.order_id for o in recorder.order_change_events() if _status(o) == "CANCELLED"}
            return created.issubset(seen)

        assert await wait_until(
            _saw_cancel_for_all, timeout=15.0
        ), "orderChanges did not deliver a CANCELLED for every cancelled order"

        # ---- assertions on the recorded stream ----
        events = recorder.order_change_events()
        our_events = [o for o in events if o.order_id in created]
        opened = {o.order_id for o in our_events if _status(o) == "OPEN"}
        cancelled = {o.order_id for o in our_events if _status(o) == "CANCELLED"}

        assert opened == created, f"missing OPEN events for {created - opened}"
        assert cancelled == created, f"missing CANCELLED events for {created - cancelled}"
        assert (
            len(our_events) >= 2 * BURST_N
        ), f"expected >= {2 * BURST_N} lifecycle events for our orders, got {len(our_events)}"

        # Batching is tolerated, not required: report the observed frame shape.
        frame_sizes = recorder.order_change_frame_sizes()
        batched_frames = [n for n in frame_sizes if n > 1]
        logger.info(
            "orderChanges frames=%d sizes=%s; %d frame(s) carried >1 event (batching %s)",
            len(frame_sizes),
            frame_sizes,
            len(batched_frames),
            "observed" if batched_frames else "not observed this run",
        )

        # Ordering preserved within and across frames: sequenceNumber strictly
        # increases along the flattened arrival order.
        seqs = [o.sequence_number for o in events if o.sequence_number is not None]
        assert seqs == sorted(seqs), f"sequenceNumber not monotonic across frames: {seqs}"
        assert len(set(seqs)) == len(seqs), f"duplicate sequenceNumbers across frames: {seqs}"
        logger.info("sequenceNumbers strictly increasing across %d frames: OK", len(frame_sizes))

        # Final WS-derived open set == REST openOrders (both empty after cancels).
        latest_status: dict[str, str] = {}
        for o in events:
            if o.order_id in created:
                latest_status[o.order_id] = _status(o)
        ws_still_open = {oid for oid, st in latest_status.items() if st == "OPEN"}
        rest_final = {o.order_id for o in await spot_tester.client.get_open_orders() if o.symbol == spot_config.symbol}
        assert ws_still_open == (
            rest_final & created
        ), f"WS open set {ws_still_open} disagrees with REST {rest_final & created}"
        assert not (rest_final & created), f"REST still shows our orders open: {rest_final & created}"
        logger.info("T1 PASS: 2N=%d events delivered, ordered, REST/WS agree", 2 * BURST_N)
    finally:
        recorder.close()
        await spot_tester.orders.close_all(fail_if_none=False)
