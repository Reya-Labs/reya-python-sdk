from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.subscribe_message_type import SubscribeMessageType
class SubscribeMessagePayload(BaseModel): 
  type: SubscribeMessageType = Field(description='''Message type for subscribe messages''')
  channel: str = Field(description='''Channel path to subscribe to''')
  id: Optional[str] = Field(description='''Optional request identifier''', default=None)
