from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.cancel_all_message_type import CancelAllMessageType
from sdk.async_exec_api.mass_cancel_response import MassCancelResponse
from sdk.async_exec_api.request_error import RequestError
class CancelAllResponseMessagePayload(BaseModel): 
  type: CancelAllMessageType = Field(description='''Message type for cancelAll request and response''')
  id: str = Field(description='''Echoes the request `id`.''')
  ok: bool = Field(description='''True on success (with `payload`), false on failure (with `error`).''')
  payload: Optional[MassCancelResponse] = Field(default=None)
  error: Optional[RequestError] = Field(default=None)
