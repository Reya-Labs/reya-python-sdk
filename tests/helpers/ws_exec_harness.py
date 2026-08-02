"""Raw ws-exec WebSocket harness shared by the transport-level e2e suites.

The high-level :class:`ReyaWsExecClient` rejects malformed payloads locally
(and never builds tampered signatures), so negative probes of the SERVER's
validation must go over a short-lived raw WebSocket with a hand-built,
correctly-signed envelope. These helpers were promoted from
tests/ws_exec/test_ws_exec.py once the cod / modify / post-only ws-exec
variants needed them too.
"""

from __future__ import annotations

import json
import ssl
import time

from websocket import WebSocket, create_connection  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module

RECV_TIMEOUT_S = 15.0


def raw_connect(url: str) -> WebSocket:
    sslopt = {"cert_reqs": ssl.CERT_REQUIRED}
    return create_connection(url, sslopt=sslopt)


def raw_send_envelope(ws: WebSocket, msg_type: str, env_id: str, payload: dict) -> None:
    # Drop None-valued fields so a raw frame matches what the high-level OpenAPI
    # client puts on the wire (it serializes with exclude_none). Notably the
    # ws-exec server rejects a *present* null `reduceOnly` on spot with
    # INPUT_VALIDATION ("reduceOnly field is not supported for spot markets").
    clean = {k: v for k, v in payload.items() if v is not None}
    ws.send(json.dumps({"type": msg_type, "id": env_id, "payload": clean}))


def raw_recv_until(
    ws: WebSocket,
    predicate,
    timeout_s: float = RECV_TIMEOUT_S,
) -> dict:
    """Read frames until ``predicate(frame)`` returns True. Times out cleanly."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ws.settimeout(max(0.1, deadline - time.time()))
        try:
            raw = ws.recv()
        except Exception as exc:  # noqa: BLE001 — surface as RuntimeError below
            raise RuntimeError(f"raw recv failed: {exc}") from exc
        if not raw:
            continue
        frame: dict = json.loads(raw)
        if frame.get("type") == "ping":
            pong: dict = {"type": "pong"}
            if frame.get("id") is not None:
                pong["id"] = frame["id"]
            ws.send(json.dumps(pong))
            continue
        if predicate(frame):
            return frame
    raise RuntimeError("raw recv timed out before predicate matched")


def assert_top_level_error(frame: dict, expected_code: str, op_label: str) -> None:
    if frame.get("type") != "error":
        raise RuntimeError(f"[{op_label}] expected type=error, got {frame!r}")
    err = frame.get("error", {}) or {}
    actual = err.get("error")
    if actual != expected_code:
        raise RuntimeError(f"[{op_label}] expected {expected_code!r}, got {actual!r} message={err.get('message')!r}")


def assert_per_op_error(frame: dict, expected_codes: tuple[str, ...], op_label: str) -> dict:
    if frame.get("ok"):
        raise RuntimeError(f"[{op_label}] expected error, got ok=true payload={frame.get('payload')!r}")
    err = frame.get("error", {}) or {}
    actual = err.get("error")
    if actual not in expected_codes:
        raise RuntimeError(
            f"[{op_label}] expected one of {expected_codes!r}, got error={actual!r} message={err.get('message')!r}"
        )
    return err
