from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.pong_message_type import PongMessageType
class PongMessagePayload(BaseModel): 
  type: PongMessageType = Field(description='''Message type for pong messages''')
  timestamp: Optional[int] = Field(description='''Optional timestamp in milliseconds''', default=None)
