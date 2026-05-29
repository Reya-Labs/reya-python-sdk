from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.create_order_message_type import CreateOrderMessageType
from sdk.async_exec_api.create_order_response import CreateOrderResponse
from sdk.async_exec_api.request_error import RequestError
class CreateOrderResponseMessagePayload(BaseModel): 
  type: CreateOrderMessageType = Field(description='''Message type for createOrder request and response''')
  id: str = Field(description='''Echoes the request `id`.''')
  ok: bool = Field(description='''True on success (with `payload`), false on failure (with `error`).''')
  payload: Optional[CreateOrderResponse] = Field(default=None)
  error: Optional[RequestError] = Field(default=None)
