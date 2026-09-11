from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.cancel_all_after_message_type import CancelAllAfterMessageType
from sdk.async_exec_api.cancel_all_after_request import CancelAllAfterRequest
class CancelAllAfterRequestMessagePayload(BaseModel): 
  type: CancelAllAfterMessageType = Field(description='''Message type for cancelAllAfter request and response''')
  id: str = Field(description='''Client-chosen correlation identifier; must be unique across in-flight requests on the connection.''')
  payload: CancelAllAfterRequest = Field(description='''Arm, refresh, or disarm the account's cancel-all-after countdown using a signed request. See POST /v2/cancelAllAfter for affected orders, refresh rules, disconnect behavior, and throttling.''')
