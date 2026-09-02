from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.cancel_order_message_type import CancelOrderMessageType
from sdk.async_exec_api.cancel_order_request import CancelOrderRequest
class CancelOrderRequestMessagePayload(BaseModel): 
  type: CancelOrderMessageType = Field(description='''Message type for cancelOrder request and response''')
  id: str = Field(description='''Client-chosen correlation identifier; must be unique across in-flight requests on the connection.''')
  payload: CancelOrderRequest = Field(description='''Cancels a live order by `orderId`, or by a non-zero `clientOrderId` when `orderId` is absent. Spot, perp, and protective stops all route through the matching engine on the unified `marketId` namespace. For a `STOP_LOSS` / `TAKE_PROFIT`, cancellation is bound to the arming signer: the wallet that signs this cancel must be the exact wallet that armed the trigger, so a different signer holding trade permission on the same account cannot cancel it and is rejected with `UNAUTHORIZED_ACCOUNT_ERROR`. The same binding applies to a modify against an armed trigger.''')
