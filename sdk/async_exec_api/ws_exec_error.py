from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_exec_api.ws_exec_error_code import WsExecErrorCode
class WsExecError(BaseModel): 
  error: WsExecErrorCode = Field(description='''Framing-layer error codes emitted in the top-level `error` envelope. Operation-level errors (`RequestErrorCode` from `trading-schemas.json`) ride the per-operation `*ResponseMessage` `{ ok: false, error }` shape instead.''')
  message: str = Field(description='''Human-readable error message.''')
