from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.cancel_all_after_message_type import CancelAllAfterMessageType
from sdk.async_exec_api.cancel_all_after_response import CancelAllAfterResponse
from sdk.async_exec_api.request_error import RequestError
class CancelAllAfterResponseMessagePayload(BaseModel): 
  type: CancelAllAfterMessageType = Field(description='''Message type for cancelAllAfter request and response''')
  id: str = Field(description='''Echoes the request `id`.''')
  ok: bool = Field(description='''True on success (with `payload`), false on failure (with `error`).''')
  payload: Optional[CancelAllAfterResponse] = Field(default=None)
  error: Optional[RequestError] = Field(default=None)
