"""A reconnecting, staleness-aware mark-price feed for one perp market.

Subscribes to ``/v2/perpMarket/{symbol}/summary`` on the WebSocket Info API and hands
each new mark price to asyncio. The SDK reconnects and re-subscribes on its own after a
``1012``/``1013`` close; if it runs out of reconnect attempts, this feed starts it again.

A mark older than ``MAX_MARK_AGE_MS`` counts as stale: the same 10-second limit Reya's
own stop orders use. The mark is recalculated once a second, so a fresh mark is never
more than about a second behind the venue's own triggers.

No signer key is needed: market data is public.

Usage (prints each new mark):
    python -m examples.strategies.mark_feed ETHRUSDPERP
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from dataclasses import dataclass
from decimal import Decimal

from sdk.async_api.error_message_payload import ErrorMessagePayload
from sdk.async_api.market_summary_update_payload import MarketSummaryUpdatePayload
from sdk.reya_websocket import ReyaSocket
from sdk.reya_websocket.config import WebSocketConfig

logger = logging.getLogger("mark_feed")

MAX_MARK_AGE_MS = 10_000
MAX_FUTURE_SKEW_MS = 5_000
RESTART_DELAY_S = 5
QUEUE_SIZE = 100


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class Mark:
    """One mark price and the venue time it was computed at (milliseconds)."""

    price: Decimal
    at_ms: int

    def age_ms(self, now: int | None = None) -> int:
        return (now if now is not None else now_ms()) - self.at_ms

    def is_fresh(self, now: int | None = None) -> bool:
        # A mark far in the future means this machine's clock is off: treat it as unusable.
        return -MAX_FUTURE_SKEW_MS <= self.age_ms(now) <= MAX_MARK_AGE_MS


class MarkFeed:
    """Latest-value mark feed: a slow consumer drops the oldest marks, never the newest."""

    def __init__(self, symbol: str, config: WebSocketConfig | None = None) -> None:
        self.symbol = symbol
        self._config = config or WebSocketConfig.from_env()
        self._queue: asyncio.Queue[Mark] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._socket: ReyaSocket | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._last_at_ms = 0

    # Runs on the event loop thread.
    def _push(self, mark: Mark) -> None:
        if mark.at_ms <= self._last_at_ms:
            return  # the summary repeats a mark between recalculations; keep new ones only
        self._last_at_ms = mark.at_ms
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(mark)

    # The SDK calls these on its own thread.
    def _on_open(self, ws: ReyaSocket) -> None:
        logger.info("connected; subscribing to %s summary", self.symbol)
        ws.market.summary(self.symbol).subscribe()

    def _on_message(self, _ws: ReyaSocket, message: object) -> None:
        if isinstance(message, MarketSummaryUpdatePayload):
            summary = message.data
            if summary.mark_price is None:
                return
            at_ms = summary.prices_updated_at or summary.updated_at
            mark = Mark(Decimal(summary.mark_price), int(at_ms))
            loop = self._loop
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(self._push, mark)
        elif isinstance(message, ErrorMessagePayload):
            logger.warning("feed error: %s", message.message)

    def _on_close(self, _ws: ReyaSocket, code: int | None, reason: str | None) -> None:
        # 1012 (restart or max connection age) and 1013 (slow consumer): the SDK
        # reconnects and replays the subscription by itself.
        logger.info("connection closed: %s %s", code, reason)

    async def run(self) -> None:
        """Keep the feed connected until ``stop()``."""
        self._loop = asyncio.get_running_loop()
        self._socket = ReyaSocket(
            config=self._config,
            on_open=self._on_open,
            on_message=self._on_message,
            on_close=self._on_close,
        )
        while not self._stop.is_set():
            # Blocks (in a worker thread) until the SDK gives up reconnecting or stop() is called.
            await asyncio.to_thread(self._socket.connect, None, True)
            if self._stop.is_set():
                break
            logger.warning("feed stopped reconnecting; starting it again in %s s", RESTART_DELAY_S)
            await asyncio.sleep(RESTART_DELAY_S)

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            self._socket.close()

    async def next_mark(self, timeout: float = 1.0) -> Mark | None:
        """The next new mark, or None if none arrived within ``timeout`` seconds."""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None


async def _print_marks(symbol: str) -> None:
    feed = MarkFeed(symbol)
    task = asyncio.create_task(feed.run())
    last_fresh = now_ms()
    try:
        while True:
            mark = await feed.next_mark()
            if mark is not None and mark.is_fresh():
                last_fresh = now_ms()
                logger.info("%s mark %s (%d ms old)", symbol, mark.price, mark.age_ms())
            elif now_ms() - last_fresh > MAX_MARK_AGE_MS:
                logger.warning("no fresh mark for %d s: treat the price as unknown", (now_ms() - last_fresh) // 1000)
    finally:
        feed.stop()
        task.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        asyncio.run(_print_marks(sys.argv[1] if len(sys.argv) > 1 else "ETHRUSDPERP"))
    except KeyboardInterrupt:
        pass
