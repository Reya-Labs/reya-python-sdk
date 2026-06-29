from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.channel_data_message_type import ChannelDataMessageType
from sdk.async_api.collateral_oracle_prices_channel import CollateralOraclePricesChannel
from sdk.async_api.collateral_oracle_price import CollateralOraclePrice
class CollateralOraclePricesUpdatePayload(BaseModel): 
  type: ChannelDataMessageType = Field(description='''Message type for channel data updates''')
  timestamp: float = Field(description='''Update timestamp (milliseconds)''')
  channel: CollateralOraclePricesChannel = Field(description='''Channel for collateral oracle price updates''')
  data: List[CollateralOraclePrice] = Field()
