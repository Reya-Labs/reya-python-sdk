from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.cancel_all_message_type import CancelAllMessageType
from sdk.async_exec_api.mass_cancel_request import MassCancelRequest
class CancelAllRequestMessagePayload(BaseModel): 
  type: CancelAllMessageType = Field(description='''Message type for cancelAll request and response''')
  id: str = Field(description='''Client-chosen correlation identifier; must be unique across in-flight requests on the connection.''')
  payload: MassCancelRequest = Field(description='''Request to cancel all orders matching the specified filters''')
