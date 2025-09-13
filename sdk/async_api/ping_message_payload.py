from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.ping_message_type import PingMessageType
class PingMessagePayload(BaseModel): 
  type: PingMessageType = Field(description='''Message type for ping messages''')
  timestamp: Optional[int] = Field(description='''Optional timestamp in milliseconds''', default=None)
