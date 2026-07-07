from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.channel_data_message_type import ChannelDataMessageType
from sdk.async_api.deprecated_prices_channel import DeprecatedPricesChannel
from sdk.async_api.price import Price
class PricesUpdatePayload(BaseModel): 
  type: ChannelDataMessageType = Field(description='''Message type for channel data updates''')
  timestamp: float = Field(description='''Update timestamp (milliseconds)''')
  channel: DeprecatedPricesChannel = Field(description='''Deprecated legacy price channel. Use `/v2/assetOraclePrices`.''')
  data: List[Price] = Field()
