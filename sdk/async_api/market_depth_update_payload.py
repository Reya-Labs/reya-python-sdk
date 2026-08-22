from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.channel_data_message_type import ChannelDataMessageType
from sdk.async_api.depth_update import DepthUpdate
class MarketDepthUpdatePayload(BaseModel): 
  type: ChannelDataMessageType = Field(description='''Message type for channel data updates''')
  timestamp: float = Field(description='''Update timestamp (milliseconds)''')
  channel: str = Field(description='''Channel pattern for market depth snapshots''')
  data: DepthUpdate = Field(description='''An exact diff from the previous bounded depth view to the next. Either side may be empty, but at least one side contains a changed level. Update arrays are not capped at the WebSocket view size of 100 because one boundary transition can remove old visible levels and add replacement levels in the same frame.''')
