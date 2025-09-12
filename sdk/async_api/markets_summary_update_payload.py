from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.channel_data_message_type import ChannelDataMessageType
from sdk.async_api.markets_summary_channel import MarketsSummaryChannel
from sdk.async_api.market_summary import MarketSummary
class MarketsSummaryUpdatePayload(BaseModel): 
  type: ChannelDataMessageType = Field(description='''Message type for channel data updates''')
  timestamp: float = Field(description='''Update timestamp (milliseconds)''')
  channel: MarketsSummaryChannel = Field(description='''Channel for all markets summary updates''')
  data: List[MarketSummary] = Field()
