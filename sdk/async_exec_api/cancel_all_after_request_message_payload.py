from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.cancel_all_after_message_type import CancelAllAfterMessageType
from sdk.async_exec_api.cancel_all_after_request import CancelAllAfterRequest
class CancelAllAfterRequestMessagePayload(BaseModel): 
  type: CancelAllAfterMessageType = Field(description='''Message type for cancelAllAfter request and response''')
  id: str = Field(description='''Client-chosen correlation identifier; must be unique across in-flight requests on the connection.''')
  payload: CancelAllAfterRequest = Field(description='''Arms, refreshes, or disarms the account-scoped cancel-all-after countdown (dead-man's-switch / cancel-on-disconnect). While armed, the server mass-cancels all of the account's open orders (same scope as `POST /v2/cancelAll` with no `symbol` filter) if the countdown is not refreshed with another `cancelAllAfter` call before `timeoutMs` elapses. Refresh is explicit only: order-entry traffic, WebSocket protocol pings, and app-level `ping`/`pong` frames do NOT refresh the countdown, and closing a WebSocket connection does NOT trigger it — only countdown expiry does. The switch is transport-agnostic (arm over REST, refresh over WS, or vice versa) and survives reconnects until it fires or is disarmed. `accountId`, `timeoutMs`, and `nonce` are signed via EIP-712 into the `CancelAllAfter(uint64 verifyingChainId, uint64 deadline, CancelAllAfterDetails cancelAllAfter)` envelope, where `CancelAllAfterDetails(uint64 accountId, uint64 timeoutMs, uint64 nonce)` and `deadline` is the signature validity. See `docs/eip712.md` for the signing algorithm and exact typehash strings.''')
