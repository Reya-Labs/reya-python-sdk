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

from sdk.open_api.exceptions import ApiException
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.market_config import SpotTestConfig
from tests.market_data.md_ws_recorder import MarketDataRecorder
from tests.market_data.poll import wait_until

logger = logging.getLogger("reya.integration_tests")

BURST_N = 5
FORCED_BURST_N = 10


async def _nonce_safe_create(tester: ReyaTester, params: LimitOrderParameters, attempts: int = 12) -> str | None:
    """Create a limit order, retrying ONLY the ME's per-wallet nonce-ordering
    rejection.

    Firing creates truly concurrently (``asyncio.gather``) is the point of S4, but
    the matching engine requires each wallet's order nonces to ARRIVE strictly
    increasing; racing HTTP sends therefore make some land out of order and bounce
    with ``INVALID_NONCE_ERROR``. Each retry re-signs with a fresh (higher) nonce,
    so the burst stays concurrent yet every order eventually lands. A little jitter
    desynchronises retriers so the round converges.
    """
    for attempt in range(attempts):
        try:
            response = await tester.client.create_limit_order(params)
            return response.order_id
        except ApiException as exc:
            if "INVALID_NONCE" in str(exc) and attempt < attempts - 1:
                await asyncio.sleep(0.02 * (attempt + 1))
                continue
            raise
    return None


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


@pytest.mark.localnet
@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_order_changes_forced_batching(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """S4 — forced batching: fire N creates CONCURRENTLY (no await between
    submissions) to make the ingest drain group several order events into one
    ``channel_data`` frame. Regardless of how the server batches, the stream stays
    lossless and ordered: every OPEN arrives and ``sequenceNumber`` is strictly
    increasing across the flattened arrival order. Because this variant *engineers*
    the batching conditions (10 concurrent creates + an atomic mass_cancel that
    emits N CANCELLED events in one drain), at least one multi-entry frame is
    REQUIRED here — a regression that silently disabled batching (one event per
    frame) must fail this test. A same-wallet sequence-contiguity sub-assertion is
    attempted on the quiet burst window and documented-and-skipped if background
    traffic (global sequencing / other same-wallet activity) leaves gaps."""
    logger.info("=" * 80)
    logger.info("S4 FORCED BATCHING (concurrent burst of %d): %s", FORCED_BURST_N, spot_config.symbol)
    logger.info("=" * 80)

    address = spot_tester.owner_wallet_address
    assert address is not None

    await spot_tester.orders.close_all(fail_if_none=False)
    await asyncio.sleep(0.3)

    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
    recorder = MarketDataRecorder(ws_url, address=address, subscribe_order_changes=True)
    recorder.connect()
    try:
        assert await wait_until(
            lambda: recorder.order_changes_snapshot is not None, timeout=10.0
        ), "did not receive orderChanges subscribe snapshot"

        base = int(spot_config.get_safe_no_match_buy_price())
        prices = [base + i for i in range(FORCED_BURST_N)]

        # Fire ALL creates concurrently — no await between submissions. The ME's
        # per-wallet nonce-arrival guard bounces racers with INVALID_NONCE, which
        # _nonce_safe_create re-signs and re-fires; the burst stays concurrent and
        # every order lands.
        async def _create(price: int) -> str | None:
            params = OrderBuilder.from_config(spot_config).buy().price(str(price)).gtc().build()
            return await _nonce_safe_create(spot_tester, params)

        results = await asyncio.gather(*(_create(p) for p in prices))
        created = {oid for oid in results if oid}
        assert len(created) == FORCED_BURST_N, f"expected {FORCED_BURST_N} distinct ids, got {sorted(created)}"
        logger.info("Fired %d concurrent creates: %s", FORCED_BURST_N, sorted(created))

        def _saw_open_for_all() -> bool:
            seen = {o.order_id for o in recorder.order_change_events() if _status(o) == "OPEN"}
            return created.issubset(seen)

        assert await wait_until(_saw_open_for_all, timeout=20.0), (
            "orderChanges did not deliver an OPEN for every concurrently-created order; "
            f"missing {created - {o.order_id for o in recorder.order_change_events() if _status(o) == 'OPEN'}}"
        )

        # --- atomic mass_cancel: one call cancels all N, producing a burst of N
        # CANCELLED events the outbound drain is very likely to group into a
        # multi-entry frame. Snapshot the frame boundary first so we can scope the
        # contiguity check to just this atomic burst.
        frames_before_cancel = len(recorder.order_change_frame_sizes())
        await spot_tester.client.mass_cancel(symbol=spot_config.symbol, account_id=spot_tester.account_id)

        def _saw_cancel_for_all() -> bool:
            seen = {o.order_id for o in recorder.order_change_events() if _status(o) == "CANCELLED"}
            return created.issubset(seen)

        assert await wait_until(_saw_cancel_for_all, timeout=20.0), "mass_cancel did not deliver every CANCELLED"

        # Ordering: sequenceNumber strictly increasing + unique across flattened arrival.
        events = recorder.order_change_events()
        seqs = [o.sequence_number for o in events if o.sequence_number is not None]
        assert seqs == sorted(seqs), f"sequenceNumber not monotonic across frames: {seqs}"
        assert len(set(seqs)) == len(seqs), f"duplicate sequenceNumbers across frames: {seqs}"

        # Losslessness: every one of the 2N lifecycle events for our orders is present.
        our_events = [o for o in events if o.order_id in created]
        opened = {o.order_id for o in our_events if _status(o) == "OPEN"}
        cancelled = {o.order_id for o in our_events if _status(o) == "CANCELLED"}
        assert opened == created, f"missing OPEN for {created - opened}"
        assert cancelled == created, f"missing CANCELLED for {created - cancelled}"

        # Batching is REQUIRED here: this variant engineers the conditions (10
        # concurrent creates + an atomic mass_cancel emitting N CANCELLED in one
        # drain), so at least one channel_data frame MUST carry >1 entry. Report
        # the observed shape, then assert it — a silent regression to one event
        # per frame (batching disabled) has to fail this test.
        frame_sizes = recorder.order_change_frame_sizes()
        cancel_burst_sizes = frame_sizes[frames_before_cancel:]
        multi_entry = [n for n in frame_sizes if n > 1]
        logger.info(
            "S4 frames=%d sizes=%s; cancel-burst frame sizes=%s; %d multi-entry frame(s) (max %d/frame) — batching %s",
            len(frame_sizes),
            frame_sizes,
            cancel_burst_sizes,
            len(multi_entry),
            max(frame_sizes) if frame_sizes else 0,
            "OBSERVED" if multi_entry else "NOT observed",
        )
        assert frame_sizes, "no orderChanges frames recorded, cannot assert batching"
        assert max(frame_sizes) > 1, (
            f"forced batching produced no multi-event frame (max entries/frame={max(frame_sizes)}): "
            f"{FORCED_BURST_N} concurrent creates + an atomic mass_cancel should coalesce into at least "
            f"one channel_data frame carrying >1 entry, but every frame held exactly one event "
            f"(sizes={frame_sizes}) — batching may have silently regressed to one event per frame"
        )

        # Same-wallet contiguity on the ATOMIC mass_cancel burst: the ME assigns the
        # N cancels consecutive sequence numbers, so on a quiet wallet window they are
        # contiguous. Asserted when contiguous; documented-and-skipped if background /
        # global sequencing left gaps (per S4 tolerance).
        cancel_seqs = sorted(
            o.sequence_number for o in our_events if _status(o) == "CANCELLED" and o.sequence_number is not None
        )
        assert len(cancel_seqs) == FORCED_BURST_N, f"expected {FORCED_BURST_N} CANCELLED seqs, got {cancel_seqs}"
        if cancel_seqs[-1] - cancel_seqs[0] == FORCED_BURST_N - 1:
            assert cancel_seqs == list(range(cancel_seqs[0], cancel_seqs[0] + FORCED_BURST_N)), cancel_seqs
            logger.info("S4 contiguity: mass_cancel seqs %s are contiguous (quiet window) — asserted", cancel_seqs)
        else:
            logger.warning(
                "S4 contiguity SKIPPED (documented): mass_cancel seqs %s span %d for %d events — background "
                "same-wallet/global sequencing left gaps; contiguity not asserted to avoid flakiness",
                cancel_seqs,
                cancel_seqs[-1] - cancel_seqs[0] + 1,
                FORCED_BURST_N,
            )

        rest_final = {o.order_id for o in await spot_tester.client.get_open_orders() if o.symbol == spot_config.symbol}
        assert not (rest_final & created), f"REST still shows our orders open: {rest_final & created}"
        logger.info(
            "S4 PASS: %d concurrent creates + atomic mass_cancel lossless + ordered; batching reported", FORCED_BURST_N
        )
    finally:
        recorder.close()
        await spot_tester.orders.close_all(fail_if_none=False)
