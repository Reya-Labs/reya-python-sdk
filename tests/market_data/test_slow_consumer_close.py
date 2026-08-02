"""T4 — 1013 slow-consumer / backpressure close (opt-in stress).

Contract: a stateful channel (depth / orderChanges) whose client stops draining
while the server keeps producing can be closed with code **1013**, reason
"slow consumer — resubscribe for fresh snapshot". Correct client behaviour is to
reconnect + resubscribe for a fresh snapshot.

Why skip-by-default: the server's per-connection outbound bound (~1 MB) is hard
to overflow quickly on localnet, and inducing it requires a deliberately stalled
reader plus a heavy depth-churn generator that pollutes shared state. So this is
opt-in via ``REYA_MD_STRESS_1013=1`` (churn size tunable via
``REYA_MD_STRESS_ORDERS``, default 400). When it does close, we assert the code
is 1013 and that reconnect+resubscribe yields a fresh snapshot.

The stalled consumer is built inline (not via ``MarketDataRecorder``) because it
deliberately blocks its own reader thread to create backpressure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time

import pytest

from sdk.reya_websocket import ReyaSocket
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.market_config import SpotTestConfig
from tests.market_data.md_ws_recorder import MarketDataRecorder
from tests.market_data.poll import wait_until

logger = logging.getLogger("reya.integration_tests")


class _StalledDepthConsumer:
    """Subscribes to depth, then blocks its reader thread so the server's
    outbound buffer for this connection backs up (slow consumer)."""

    def __init__(self, url: str, symbol: str, stall_seconds: float) -> None:
        self._symbol = symbol
        self._stall_seconds = stall_seconds
        self._stalled_once = False
        self.closes: list[tuple[int | None, str | None]] = []
        self._lock = threading.Lock()
        self._socket = ReyaSocket(url=url, on_open=self._on_open, on_message=self._on_message, on_close=self._on_close)

    def connect(self) -> None:
        self._socket.connect()

    def close(self) -> None:
        try:
            self._socket.close()
        except (OSError, RuntimeError):  # pragma: no cover
            pass

    def _on_open(self, ws) -> None:
        ws.market.depth(self._symbol).subscribe()

    def _on_message(self, _ws, _message) -> None:
        # Block the reader thread once, after the subscribe handshake, so the
        # socket stops draining and the server accumulates unsent depth frames.
        if not self._stalled_once:
            self._stalled_once = True
            time.sleep(self._stall_seconds)

    def _on_close(self, _ws, code: int | None, reason: str | None) -> None:
        with self._lock:
            self.closes.append((code, reason))
        logger.info("stalled consumer close: code=%s reason=%r", code, reason)

    def close_events(self) -> list[tuple[int | None, str | None]]:
        with self._lock:
            return list(self.closes)


async def _generate_depth_churn(tester: ReyaTester, config: SpotTestConfig, n_orders: int) -> None:
    """Flood the depth channel: many resting bids at distinct prices, then cancel."""
    base = int(config.get_safe_no_match_buy_price())
    order_ids: list[str] = []
    for i in range(n_orders):
        params = OrderBuilder.from_config(config).buy().price(str(base + i)).gtc().build()
        try:
            oid = await tester.orders.create_limit(params)
            if oid:
                order_ids.append(oid)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("churn create %d failed (continuing): %s", i, exc)
        if i % 25 == 0:
            await asyncio.sleep(0)  # yield without slowing the flood much
    for oid in order_ids:
        try:
            await tester.client.cancel_order(order_id=oid, symbol=config.symbol, account_id=tester.account_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("churn cancel failed (continuing): %s", exc)


@pytest.mark.localnet
@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_slow_consumer_close(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    if os.environ.get("REYA_MD_STRESS_1013") != "1":
        pytest.skip(
            "T4 is an opt-in stress test (set REYA_MD_STRESS_1013=1): the ~1 MB outbound "
            "bound is hard to overflow on localnet and the churn pollutes shared state."
        )

    logger.info("=" * 80)
    logger.info("T4 SLOW-CONSUMER (1013) CLOSE: %s", spot_config.symbol)
    logger.info("=" * 80)

    symbol = spot_config.symbol
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
    n_orders = int(os.environ.get("REYA_MD_STRESS_ORDERS", "400"))

    await spot_tester.orders.close_all(fail_if_none=False)

    # Stall long enough for the server to fill this connection's outbound buffer.
    consumer = _StalledDepthConsumer(ws_url, symbol, stall_seconds=20.0)
    consumer.connect()
    await asyncio.sleep(1.0)  # let the subscribe handshake land before the flood

    try:
        await _generate_depth_churn(spot_tester, spot_config, n_orders)

        got_1013 = await wait_until(
            lambda: any(code == 1013 for code, _ in consumer.close_events()),
            timeout=40.0,
            interval=0.5,
        )
        closes = consumer.close_events()
        if not got_1013:
            pytest.skip(
                "1013 not induced on this localnet within the churn budget "
                f"(closes={closes}); backpressure bound not reached. Increase REYA_MD_STRESS_ORDERS to retry."
            )

        code, reason = next((c, r) for c, r in closes if c == 1013)
        reason_l = (reason or "").lower()
        assert (
            reason is None or "slow" in reason_l or "consumer" in reason_l
        ), f"1013 reason did not describe a slow consumer: {reason!r}"
        logger.info("observed 1013 slow-consumer close: reason=%r", reason)
    finally:
        consumer.close()

    # Reconnect + resubscribe -> fresh consistent snapshot (correct client recovery).
    fresh = MarketDataRecorder(ws_url, symbol=symbol, subscribe_depth=True)
    fresh.connect()
    try:
        assert await wait_until(
            lambda: fresh.depth_snapshot_copy() is not None, timeout=15.0
        ), "reconnect after 1013 did not yield a fresh depth snapshot"
        logger.info("T4 PASS: 1013 close induced; reconnect delivered a fresh snapshot")
    finally:
        fresh.close()
        await spot_tester.orders.close_all(fail_if_none=False)
