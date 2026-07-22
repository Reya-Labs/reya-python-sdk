"""S1 — the orderChanges subscribe-snapshot IS the open-orders state (non-empty).

The ``walletOrderChanges`` subscribe handshake returns a snapshot of the wallet's
currently-resting orders; the documented contract is that this snapshot equals
``GET /v2/wallet/{address}/openOrders`` at subscribe time. T3 checks this only in
passing (and often on an empty book); this test makes the non-trivial case
explicit: rest three distinct orders first, then subscribe and assert the
snapshot's order ids AND per-order fields equal REST ``openOrders`` exactly.
"""

from __future__ import annotations

import logging
import os

import pytest

from sdk.async_api.order import Order as AsyncOrder
from sdk.open_api.models.order import Order as RestOrder
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.market_config import SpotTestConfig
from tests.market_data.md_ws_recorder import MarketDataRecorder
from tests.market_data.poll import wait_until

logger = logging.getLogger("reya.integration_tests")

# Three distinct resting bids (distinct px AND qty) so the field comparison is
# non-trivial. All safe-no-match (far below oracle) and tick/step-aligned.
_ORDERS = [("10.13", "0.111"), ("11.17", "0.222"), ("12.19", "0.333")]


def _enum(value: object) -> object:
    return value.value if hasattr(value, "value") else value


def _snapshot_fingerprint(order: AsyncOrder | RestOrder) -> tuple[object, ...]:
    """Stable per-order fields present on BOTH the async snapshot order and the
    REST order. ``sequenceNumber`` is intentionally excluded — snapshot orders
    never carry one (the boundary is ``snapshotSequenceNumber``)."""
    return (
        order.order_id,
        order.symbol,
        order.limit_px,
        order.qty,
        _enum(order.side),
        _enum(order.status),
        _enum(order.order_type),
        _enum(order.time_in_force),
    )


@pytest.mark.localnet
@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_snapshot_matches_open_orders(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    logger.info("=" * 80)
    logger.info("S1 SUBSCRIBE-SNAPSHOT == REST openOrders (non-empty): %s", spot_config.symbol)
    logger.info("=" * 80)

    symbol = spot_config.symbol
    address = spot_tester.owner_wallet_address
    assert address is not None
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")

    await spot_tester.orders.close_all(fail_if_none=False)

    created: set[str] = set()
    for px, qty in _ORDERS:
        params = OrderBuilder.from_config(spot_config).buy().price(px).qty(qty).gtc().build()
        oid = await spot_tester.orders.create_limit(params)
        assert oid, f"create_limit returned no id for {px}/{qty}"
        created.add(oid)

    # REST must show all three before we subscribe, so the snapshot is non-empty.
    async def _rest_has_all() -> bool:
        rest_ids = {o.order_id for o in await spot_tester.client.get_open_orders() if o.symbol == symbol}
        return created.issubset(rest_ids)

    assert await wait_until(_rest_has_all, timeout=20.0), "REST openOrders did not converge to the three resting orders"

    recorder = MarketDataRecorder(ws_url, address=address, subscribe_order_changes=True)
    recorder.connect()
    try:
        # Snapshot ids must match REST ids for this wallet exactly (a point-in-time
        # cursor, so poll to let both settle to the same set).
        async def _ids_agree() -> bool:
            if recorder.order_changes_snapshot is None:
                return False
            snap_ids = {o.order_id for o in recorder.order_changes_snapshot_orders()}
            rest_ids = {o.order_id for o in await spot_tester.client.get_open_orders() if o.symbol == symbol}
            return bool(snap_ids) and snap_ids == rest_ids == created

        assert await wait_until(_ids_agree, timeout=20.0), (
            "orderChanges snapshot ids != REST openOrders ids; "
            f"snapshot={sorted(o.order_id for o in recorder.order_changes_snapshot_orders())} created={sorted(created)}"
        )

        snapshot_orders = recorder.order_changes_snapshot_orders()
        assert len(snapshot_orders) == len(
            _ORDERS
        ), f"snapshot carried {len(snapshot_orders)} orders, expected {len(_ORDERS)}"

        rest_orders = [o for o in await spot_tester.client.get_open_orders() if o.symbol == symbol]
        snap_fp = {_snapshot_fingerprint(o) for o in snapshot_orders}
        rest_fp = {_snapshot_fingerprint(o) for o in rest_orders}
        assert snap_fp == rest_fp, (
            "snapshot fields differ from REST openOrders:\n"
            f"  only in snapshot: {snap_fp - rest_fp}\n"
            f"  only in REST:     {rest_fp - snap_fp}"
        )

        # And the fields are the exact ones we placed (not just internally consistent).
        placed_pairs = {(px, qty) for px, qty in _ORDERS}
        snap_pairs = {(o.limit_px, o.qty) for o in snapshot_orders}
        assert snap_pairs == placed_pairs, f"snapshot px/qty {snap_pairs} != placed {placed_pairs}"
        logger.info("S1 PASS: snapshot of %d orders == REST openOrders exactly (ids + fields)", len(snapshot_orders))
    finally:
        recorder.close()
        await spot_tester.orders.close_all(fail_if_none=False)
