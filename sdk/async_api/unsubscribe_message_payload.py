from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.unsubscribe_message_type import UnsubscribeMessageType
class UnsubscribeMessagePayload(BaseModel): 
  type: UnsubscribeMessageType = Field(description='''Message type for unsubscribe messages''')
  channel: str = Field(description='''Channel path to unsubscribe from''')
  id: Optional[str] = Field(description='''Optional request identifier''', default=None)
