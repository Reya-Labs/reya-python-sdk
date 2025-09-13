from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.subscribed_message_type import SubscribedMessageType
class SubscribedMessagePayload(BaseModel): 
  type: SubscribedMessageType = Field(description='''Message type for subscribed confirmation messages''')
  channel: str = Field(description='''Channel that was subscribed to''')
  contents: Optional[dict[str, Any]] = Field(description='''Optional initial data for the subscription''', default=None)
