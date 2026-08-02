from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_exec_api.ws_exec_error_code import WsExecErrorCode
class WsExecError(BaseModel): 
  error: WsExecErrorCode = Field(description='''Framing-layer error codes emitted in the top-level `error` envelope. Operation-level errors (`RequestErrorCode` from `trading-schemas.json`) ride the per-operation `*ResponseMessage` `{ ok: false, error }` shape instead.

  - `MALFORMED_JSON` — the frame was not valid JSON.
  - `UNKNOWN_TYPE` — the frame `type` is not a recognised operation.
  - `DUPLICATE_REQUEST_ID` — a frame reused an in-flight `id`.
  - `SERVER_SHUTTING_DOWN` — the server is draining and rejected the frame (reconnect to another instance).
  - `TOO_MANY_INFLIGHT` — the connection exceeded its per-connection in-flight request cap; retry after awaiting outstanding responses.
  - `INTERNAL` — an unexpected server-side framing error.
  ''')
  message: str = Field(description='''Human-readable error message.''')
