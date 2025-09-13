from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.unsubscribed_message_type import UnsubscribedMessageType
class UnsubscribedMessagePayload(BaseModel): 
  type: UnsubscribedMessageType = Field(description='''Message type for unsubscribed confirmation messages''')
  channel: str = Field(description='''Channel that was unsubscribed from''')
