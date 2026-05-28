from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_exec_api.ping_message_type import PingMessageType
class PingMessagePayload(BaseModel): 
  type: PingMessageType = Field(description='''Message type for ping messages''')
  id: Optional[str] = Field(description='''Optional correlation identifier echoed in the corresponding pong.''', default=None)
