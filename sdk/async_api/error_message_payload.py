from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.error_message_type import ErrorMessageType
class ErrorMessagePayload(BaseModel): 
  type: ErrorMessageType = Field(description='''Message type for error messages''')
  message: str = Field(description='''Error description''')
  channel: Optional[str] = Field(description='''Optional channel related to the error''', default=None)
