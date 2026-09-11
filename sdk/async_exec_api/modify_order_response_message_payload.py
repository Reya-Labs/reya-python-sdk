from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.modify_order_message_type import ModifyOrderMessageType
from sdk.async_exec_api.modify_order_response import ModifyOrderResponse
from sdk.async_exec_api.request_error import RequestError
class ModifyOrderResponseMessagePayload(BaseModel): 
  type: ModifyOrderMessageType = Field(description='''Message type for modifyOrder request and response''')
  id: str = Field(description='''Echoes the request `id`.''')
  ok: bool = Field(description='''True on success (with `payload`), false on failure (with `error`).''')
  payload: Optional[ModifyOrderResponse] = Field(description='''Result of a modification, with the same orderId as before. Fields report immediate execution and resulting order state. See POST /v2/modifyOrder for outcomes and POST /v2/createOrder for shared fill-correlation rules.''', default=None)
  error: Optional[RequestError] = Field(default=None)
