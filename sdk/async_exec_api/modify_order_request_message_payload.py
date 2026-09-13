from __future__ import annotations
from typing import Any, Union, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.modify_order_message_type import ModifyOrderMessageType
from sdk.async_exec_api.limit_modify_order_request import LimitModifyOrderRequest
from sdk.async_exec_api.trigger_modify_order_request import TriggerModifyOrderRequest
class ModifyOrderRequestMessagePayload(BaseModel): 
  type: ModifyOrderMessageType = Field(description='''Message type for modifyOrder request and response''')
  id: str = Field(description='''Client-chosen correlation identifier; must be unique across in-flight requests on the connection.''')
  payload: Union[LimitModifyOrderRequest, TriggerModifyOrderRequest] = Field(description='''Modify a resting order using its complete intended post-modify state and a fresh signature and nonce. Omitted fields do not inherit existing values. Target by `orderId` or a non-zero `clientOrderId`. See `POST /v2/modifyOrder` for modifiable fields, priority, and execution behavior.''')
