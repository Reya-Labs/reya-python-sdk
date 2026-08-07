"""S3 — orderChanges is wallet-scoped: A's feed never carries B's orders.

Subscribes to wallet A's ``walletOrderChanges`` and then acts on wallet B. A's
feed must never surface B's order ids or B's account id — the channel is keyed by
address and one wallet's activity must not leak into another's stream. The test is
non-trivial in both directions: A also places its own order, so the feed is proven
live (it delivers A's event) while B's concurrent activity stays absent throughout
a bounded observation window.
"""

from __future__ import annotations

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

# Bounded window (seconds) to let any erroneously-leaked cross-wallet event land
# after A's own event confirms the feed is live.
_OBSERVE_SECONDS = 6.0


@pytest.mark.localnet
@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_cross_wallet_isolation(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    logger.info("=" * 80)
    logger.info("S3 CROSS-WALLET ISOLATION (A subscribes, B acts): %s", spot_config.symbol)
    logger.info("=" * 80)

    symbol = spot_config.symbol
    wallet_a = maker_tester  # subscriber
    wallet_b = taker_tester  # actor
    address_a = wallet_a.owner_wallet_address
    assert address_a is not None
    assert wallet_a.account_id != wallet_b.account_id, "A and B must be distinct accounts"
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")

    await wallet_a.orders.close_all(fail_if_none=False)
    await wallet_b.orders.close_all(fail_if_none=False)

    recorder = MarketDataRecorder(ws_url, address=address_a, subscribe_order_changes=True)
    recorder.connect()
    try:
        assert await wait_until(
            lambda: recorder.order_changes_snapshot is not None, timeout=15.0
        ), "did not receive A's orderChanges snapshot"

        # B rests orders at safe-no-match prices (same side as any A order, so they
        # never cross — pure resting activity on B's own book).
        b_created: set[str] = set()
        for px in ["12.31", "12.43", "12.57"]:
            params = OrderBuilder.from_config(spot_config).buy().price(px).gtc().build()
            oid = await wallet_b.orders.create_limit(params)
            assert oid, f"B create_limit returned no id for {px}"
            b_created.add(oid)

        # A places its own order — the feed must deliver THIS (proving it is live).
        a_params = OrderBuilder.from_config(spot_config).buy().price("10.19").gtc().build()
        a_id = await wallet_a.orders.create_limit(a_params)
        assert a_id, "A create_limit returned no id"

        assert await wait_until(
            lambda: any(o.order_id == a_id for o in recorder.order_change_events()), timeout=15.0
        ), "A's feed never delivered A's own order — subscription not live, isolation check would be vacuous"

        # Cancel B's orders too (more B activity to potentially leak).
        for oid in b_created:
            await wallet_b.client.cancel_order(order_id=oid, symbol=symbol, account_id=wallet_b.account_id)

        # Bounded observation: give any leaked B event time to arrive.
        await asyncio.sleep(_OBSERVE_SECONDS)

        events = recorder.order_change_events()
        snapshot_ids = {o.order_id for o in recorder.order_changes_snapshot_orders()}
        leaked_by_id = [o.order_id for o in events if o.order_id in b_created] + [
            oid for oid in snapshot_ids if oid in b_created
        ]
        leaked_by_account = [o.order_id for o in events if o.account_id == wallet_b.account_id]
        assert not leaked_by_id, f"A's feed leaked B's order ids: {leaked_by_id}"
        assert not leaked_by_account, f"A's feed carried B's account_id {wallet_b.account_id}: {leaked_by_account}"

        # Every event A did see belongs to A.
        foreign_accounts = {o.account_id for o in events if o.account_id != wallet_a.account_id}
        assert not foreign_accounts, f"A's feed carried foreign account ids: {foreign_accounts}"
        logger.info(
            "S3 PASS: A saw its own order and %d total events, none from B (account %s) over %.0fs",
            len(events),
            wallet_b.account_id,
            _OBSERVE_SECONDS,
        )
    finally:
        recorder.close()
        await wallet_a.orders.close_all(fail_if_none=False)
        await wallet_b.orders.close_all(fail_if_none=False)
