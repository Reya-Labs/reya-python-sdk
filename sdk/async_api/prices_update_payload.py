from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.channel_data_message_type import ChannelDataMessageType
from sdk.async_api.prices_channel import PricesChannel
from sdk.async_api.price import Price
class PricesUpdatePayload(BaseModel): 
  type: ChannelDataMessageType = Field(description='''Message type for channel data updates''')
  timestamp: float = Field(description='''Update timestamp (milliseconds)''')
  channel: PricesChannel = Field(description='''Channel for all prices updates''')
  data: List[Price] = Field()
