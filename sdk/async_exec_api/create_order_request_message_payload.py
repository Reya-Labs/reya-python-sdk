from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.create_order_message_type import CreateOrderMessageType
from sdk.async_exec_api.create_order_request import CreateOrderRequest
class CreateOrderRequestMessagePayload(BaseModel): 
  type: CreateOrderMessageType = Field(description='''Message type for createOrder request and response''')
  id: str = Field(description='''Client-chosen correlation identifier; must be unique across in-flight requests on the connection.''')
  payload: CreateOrderRequest = Field()
