from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.channel_data_message_type import ChannelDataMessageType
from sdk.async_api.asset_oracle_prices_channel import AssetOraclePricesChannel
from sdk.async_api.asset_oracle_price import AssetOraclePrice
class AssetOraclePricesUpdatePayload(BaseModel):
  type: ChannelDataMessageType = Field(description='''Message type for channel data updates''')
  timestamp: float = Field(description='''Update timestamp (milliseconds)''')
  channel: AssetOraclePricesChannel = Field(description='''Channel for asset oracle price updates''')
  data: List[AssetOraclePrice] = Field()
