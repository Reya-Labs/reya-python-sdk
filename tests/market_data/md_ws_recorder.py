"""Frame-level WebSocket recorder for market-data pipeline tests.

The shared ``ReyaTester`` WebSocket state (``tests/helpers/reya_tester/websocket.py``)
collapses every incoming frame into ``EventStore``s, which is perfect for
"does the final state contain X" assertions but discards two things these
tests need to observe directly:

1. **Frame boundaries** — batching means one ``channel_data`` frame may carry
   several entries in ``data[]``. To assert "all events arrived, batching
   tolerated, order preserved within and across frames" we must keep each frame
   as a distinct list, in arrival order.
2. **Close codes** — the 1012 (feed resync) / 1013 (slow consumer) contract is a
   WebSocket *close frame*; ``ReyaTester`` uses the SDK default ``on_close`` which
   only logs. We record ``(code, reason)`` so a test can assert on them.

This is test-only infrastructure built on the public ``ReyaSocket`` — no SDK
library change. Callbacks fire on the ``websocket-client`` reader thread, so all
recorded state is guarded by a lock and copied out for the asyncio test thread.
"""

from __future__ import annotations

from typing import Any, Optional

import json
import logging
import threading

from sdk.async_api.depth import Depth
from sdk.async_api.error_message_payload import ErrorMessagePayload
from sdk.async_api.market_depth_update_payload import MarketDepthUpdatePayload
from sdk.async_api.order import Order as AsyncOrder
from sdk.async_api.order_change_update_payload import OrderChangeUpdatePayload
from sdk.async_api.order_changes_snapshot import OrderChangesSnapshot
from sdk.async_api.order_changes_subscribed_payload import OrderChangesSubscribedPayload
from sdk.async_api.subscribed_message_payload import SubscribedMessagePayload
from sdk.reya_websocket import ReyaSocket, WebSocketMessage

logger = logging.getLogger("reya.integration_tests")


class MarketDataRecorder:
    """Records depth / orderChanges frames and close events from one connection.

    Pass an ``address`` to subscribe to that wallet's ``orderChanges`` and/or a
    ``symbol`` to subscribe to that market's ``depth``. ``connect()`` opens the
    socket in a background thread; ``close()`` tears it down.
    """

    def __init__(
        self,
        url: str,
        *,
        address: Optional[str] = None,
        symbol: Optional[str] = None,
        subscribe_order_changes: bool = False,
        subscribe_depth: bool = False,
    ) -> None:
        self._url = url
        self._address = address
        self._symbol = symbol
        self._sub_order_changes = subscribe_order_changes
        self._sub_depth = subscribe_depth

        self._lock = threading.Lock()

        # orderChanges: the subscribe snapshot, then each live update frame's data[].
        self.order_changes_snapshot: Optional[OrderChangesSnapshot] = None
        self.order_change_frames: list[list[AsyncOrder]] = []

        # depth: the subscribe snapshot, then each live update frame (a Depth diff).
        self.depth_snapshot: Optional[Depth] = None
        self.depth_frames: list[Depth] = []

        # Raw wire frames (pre-Pydantic) captured off the same reader thread via the
        # websocket-client ``on_data`` hook, which fires with the decoded text of every
        # TEXT frame immediately before ``on_message`` parses it. Kept as the exact
        # decoded JSON dicts so a test can assert on the literal wire strings (e.g. a
        # decimal ``"0.617"`` never silently reformatted to ``"0.6170"`` or a float),
        # independently of the typed Pydantic view.
        self.raw_frames: list[dict[str, Any]] = []

        # Control-plane observations.
        self.closes: list[tuple[Optional[int], Optional[str]]] = []
        self.errors: list[str] = []
        self.open_count = 0

        self._socket = ReyaSocket(
            url=url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_close=self._on_close,
            on_data=self._on_data,
        )

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        self._socket.connect()

    def close(self) -> None:
        try:
            self._socket.close()
        except (OSError, RuntimeError) as exc:  # pragma: no cover - teardown best effort
            logger.debug("recorder close ignored: %s", exc)

    # -- callbacks (run on the websocket reader thread) --------------------

    def _on_open(self, ws) -> None:
        with self._lock:
            self.open_count += 1
        if self._sub_order_changes and self._address:
            ws.wallet.order_changes(self._address).subscribe()
        if self._sub_depth and self._symbol:
            ws.market.depth(self._symbol).subscribe()

    def _on_message(self, _ws, message: WebSocketMessage) -> None:
        with self._lock:
            if isinstance(message, OrderChangesSubscribedPayload):
                self.order_changes_snapshot = message.contents
            elif isinstance(message, SubscribedMessagePayload):
                if "depth" in message.channel and message.contents:
                    self.depth_snapshot = Depth.model_validate(message.contents)
            elif isinstance(message, OrderChangeUpdatePayload):
                self.order_change_frames.append(list(message.data))
            elif isinstance(message, MarketDepthUpdatePayload):
                self.depth_frames.append(message.data)
            elif isinstance(message, ErrorMessagePayload):
                self.errors.append(str(message.message))

    def _on_data(self, _ws, data: Any, _data_type: Any = None, _cont: Any = None) -> None:
        # ``on_data`` sees TEXT and BINARY frames; only TEXT carries our JSON. Decode
        # bytes defensively and skip anything that is not a JSON object.
        if isinstance(data, (bytes, bytearray)):
            try:
                data = data.decode("utf-8")
            except UnicodeDecodeError:
                return
        if not isinstance(data, str):
            return
        try:
            frame = json.loads(data)
        except (ValueError, TypeError):
            return
        if isinstance(frame, dict):
            with self._lock:
                self.raw_frames.append(frame)

    def _on_close(self, _ws, code: Optional[int], reason: Optional[str]) -> None:
        with self._lock:
            self.closes.append((code, reason))
        logger.info("recorder observed close: code=%s reason=%r", code, reason)

    # -- snapshots for the test thread -------------------------------------

    def order_change_events(self) -> list[AsyncOrder]:
        """All live orderChanges events flattened in arrival order (across frames)."""
        with self._lock:
            return [order for frame in self.order_change_frames for order in frame]

    def order_change_frame_sizes(self) -> list[int]:
        with self._lock:
            return [len(frame) for frame in self.order_change_frames]

    def depth_updates(self) -> list[Depth]:
        with self._lock:
            return list(self.depth_frames)

    def close_events(self) -> list[tuple[Optional[int], Optional[str]]]:
        with self._lock:
            return list(self.closes)

    def has_close_code(self, code: int) -> bool:
        with self._lock:
            return any(c == code for c, _ in self.closes)

    def order_changes_snapshot_orders(self) -> list[AsyncOrder]:
        with self._lock:
            if self.order_changes_snapshot is None:
                return []
            return list(self.order_changes_snapshot.data)

    def depth_snapshot_copy(self) -> Optional[Depth]:
        with self._lock:
            return self.depth_snapshot

    # -- raw wire accessors (pre-Pydantic) ---------------------------------

    def raw_order_entries(self) -> list[dict[str, Any]]:
        """Every raw order dict delivered on ``orderChanges`` channel_data frames,
        flattened in arrival order (the literal wire objects, not Pydantic models)."""
        with self._lock:
            frames = list(self.raw_frames)
        entries: list[dict[str, Any]] = []
        for frame in frames:
            if frame.get("type") == "channel_data" and str(frame.get("channel", "")).endswith("/orderChanges"):
                data = frame.get("data")
                if isinstance(data, list):
                    entries.extend(item for item in data if isinstance(item, dict))
        return entries

    def raw_depth_update_levels(self, side: str) -> list[tuple[dict[str, Any], int]]:
        """Raw ``{px, qty}`` level dicts for ``side`` ("bids"/"asks") across every
        ``depth`` channel_data frame, each paired with its frame index so a test can
        pick the latest wire statement for a price."""
        with self._lock:
            frames = list(enumerate(self.raw_frames))
        levels: list[tuple[dict[str, Any], int]] = []
        for index, frame in frames:
            if frame.get("type") == "channel_data" and str(frame.get("channel", "")).endswith("/depth"):
                data = frame.get("data")
                if isinstance(data, dict):
                    for level in data.get(side, []) or []:
                        if isinstance(level, dict):
                            levels.append((level, index))
        return levels
