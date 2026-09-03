"""Raw-WebSocket probe used to trip an inbound message-rate cap.

Lives outside the test module so the same helpers can be pinned offline
(``test_offline_probe_helpers.py``) against a scripted socket: on a live run
they only ever execute inside one flood, and a probe that silently stopped
reading close frames would turn a missing 4029 into a passing test.

Everything here reads at the FRAME layer. ``WebSocket.recv`` collapses a close
frame into ``""``, which is indistinguishable from an empty text frame and
throws away the status code — and for this control the status code is the whole
signal.
"""

from __future__ import annotations

from typing import Any

import json
import time

from websocket import (  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module
    ABNF,
    WebSocket,
    WebSocketException,
    WebSocketTimeoutException,
)

# The ws-exec client's frame-layer helpers, reused deliberately: one
# implementation means the two surfaces cannot disagree about what a close
# frame said, or about when the answering write happens relative to reading it.
# They are private because no SDK caller needs them — a test harness reading
# frames by hand does.
from sdk.reya_ws_exec.client import _echo_close, _parse_close_payload, _pong

#: FLOOR under each frame read's socket timeout, mirroring ``ws_exec_harness``.
#: A read is given the whole REMAINING budget, never less than this: once the
#: deadline is all but reached, ``deadline - now`` is zero or negative, which
#: would make ``settimeout`` turn the socket non-blocking exactly when the close
#: frame is most likely still in flight. The loop's own ``while`` condition, not
#: this value, is what ends the wait.
_POLL_TIMEOUT_S = 0.1


def send_json(ws: WebSocket, payload: dict[str, Any]) -> None:
    ws.send(json.dumps(payload))


def flood_pings(ws: WebSocket, count: int) -> int:
    """Fire ``count`` JSON pings; return how many the transport accepted.

    A ping is the cheapest frame that still counts against an inbound cap, and
    both surfaces check the cap BEFORE parsing, so the flood measures the rate
    guard and nothing downstream of it. Sending stops early once the server has
    hung up — the close frame is read separately, by :func:`await_close`.
    """
    for sent in range(count):
        try:
            send_json(ws, {"type": "ping"})
        except (WebSocketException, OSError):
            return sent
    return count


def await_close(ws: WebSocket, timeout_s: float) -> tuple[int | None, str | None]:
    """Read frames until the server's close frame arrives; return (code, reason).

    ``(None, None)`` means the budget ran out or the transport died without a
    close frame — never a fabricated code, so a caller's assertion fails loudly
    instead of comparing against a default.

    Reads RAW frames for the reason the ws-exec client does (see
    :meth:`ReyaWsExecClient._reader_loop`): ``recv_data_frame`` echoes the close
    BEFORE returning it, and against a socket the server is tearing down that
    write raises — turning a 4029 that had already arrived into the transport
    death this function reports as ``(None, None)``. The code is read off the
    frame first; only then is anything written back.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ws.settimeout(max(_POLL_TIMEOUT_S, deadline - time.time()))
        try:
            frame = ws.recv_frame()
        except WebSocketTimeoutException:
            continue
        except (WebSocketException, OSError):
            return None, None
        if not frame:
            continue
        if frame.opcode == ABNF.OPCODE_CLOSE:
            code, reason = _parse_close_payload(frame.data)
            _echo_close(ws)
            return code, reason
        if frame.opcode == ABNF.OPCODE_PING:
            _pong(ws, frame.data)
    return None, None


def recv_json(ws: WebSocket, timeout_s: float) -> dict[str, Any] | None:
    """Read until one JSON object frame arrives, or the budget runs out."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ws.settimeout(max(_POLL_TIMEOUT_S, deadline - time.time()))
        try:
            opcode, frame = ws.recv_data_frame(control_frame=True)
        except WebSocketTimeoutException:
            continue
        except (WebSocketException, OSError):
            return None
        if opcode != ABNF.OPCODE_TEXT or not frame.data:
            continue
        try:
            parsed = json.loads(frame.data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def close_quietly(ws: WebSocket) -> None:
    """Close a socket that may already be dead; cleanup never masks a verdict."""
    try:
        ws.close()
    except (WebSocketException, OSError):
        pass
