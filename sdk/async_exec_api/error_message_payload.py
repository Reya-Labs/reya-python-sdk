from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_exec_api.error_message_type import ErrorMessageType
from sdk.async_exec_api.ws_exec_error import WsExecError
class ErrorMessagePayload(BaseModel): 
  type: ErrorMessageType = Field(description='''Message type for top-level error envelopes''')
  id: Optional[str] = Field(description='''Echoes the offending request's `id` when known (e.g., for in-flight id collisions); absent for frame-level errors that couldn't be parsed.''', default=None)
  error: WsExecError = Field()
