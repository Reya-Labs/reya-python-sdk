from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.subscribed_message_type import SubscribedMessageType
from sdk.async_api.depth_snapshot import DepthSnapshot
class MarketDepthSubscribedPayload(BaseModel): 
  type: SubscribedMessageType = Field(description='''Message type for subscribed confirmation messages''')
  channel: str = Field(description='''Channel pattern for market depth snapshots''')
  contents: DepthSnapshot = Field(description='''The fixed public WebSocket depth snapshot. This narrows the shared REST
  snapshot maximum from 1,000 to the 100 levels per side published by the
  WebSocket channel.
  ''')
