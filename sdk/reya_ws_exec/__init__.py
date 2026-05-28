"""High-level client for the Reya ws-exec WebSocket order-entry service.

The ws-exec service is a low-latency alternative to the REST /v2/orders
endpoints — it accepts the same EIP-712-signed payloads over a persistent
WebSocket connection and replies with per-request envelopes keyed by an
opaque request id.

This package provides :class:`ReyaWsExecClient`, which mirrors the
:class:`sdk.reya_rest_api.ReyaTradingClient` order-entry surface
(``create_limit_order``, ``create_trigger_order``, ``cancel_order``,
``mass_cancel``) so users never need to hand-craft signatures or wire
envelopes. Per-wallet nonce state is shared with ``ReyaTradingClient`` so
REST and WS-exec calls from the same process cannot collide on nonces.
"""

from sdk.reya_ws_exec.client import ReyaWsExecClient, WsExecOperationError, WsExecProtocolError

__all__ = [
    "ReyaWsExecClient",
    "WsExecOperationError",
    "WsExecProtocolError",
]
