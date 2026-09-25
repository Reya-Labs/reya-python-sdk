from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.cancel_order_message_type import CancelOrderMessageType
from sdk.async_exec_api.cancel_order_request import CancelOrderRequest
class CancelOrderRequestMessagePayload(BaseModel): 
  type: CancelOrderMessageType = Field(description='''Message type for cancelOrder request and response''')
  id: str = Field(description='''Client-chosen correlation identifier; must be unique across in-flight requests on the connection.''')
  payload: CancelOrderRequest = Field(description='''Cancel a live order by orderId, or a non-zero clientOrderId when orderId is absent. Supports spot, perp, and protective stops. See POST /v2/cancelOrder for signer requirements.''')
