from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.subscribed_message_type import SubscribedMessageType
from sdk.async_api.market_candles_snapshot import MarketCandlesSnapshot
class MarketCandlesSubscribedPayload(BaseModel): 
  type: SubscribedMessageType = Field(description='''Message type for subscribed confirmation messages''')
  channel: str = Field(description='''Channel pattern for a market's candles at one resolution''')
  contents: MarketCandlesSnapshot = Field()
