from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.cancel_order_message_type import CancelOrderMessageType
from sdk.async_exec_api.cancel_order_response import CancelOrderResponse
from sdk.async_exec_api.request_error import RequestError
class CancelOrderResponseMessagePayload(BaseModel): 
  type: CancelOrderMessageType = Field(description='''Message type for cancelOrder request and response''')
  id: str = Field(description='''Echoes the request `id`.''')
  ok: bool = Field(description='''True on success (with `payload`), false on failure (with `error`).''')
  payload: Optional[CancelOrderResponse] = Field(default=None)
  error: Optional[RequestError] = Field(default=None)
