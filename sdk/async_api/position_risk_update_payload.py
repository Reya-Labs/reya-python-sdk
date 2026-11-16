from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.channel_data_message_type import ChannelDataMessageType
from sdk.async_api.position_risk import PositionRisk
class PositionRiskUpdatePayload(BaseModel): 
  type: ChannelDataMessageType = Field(description='''Message type for channel data updates''')
  timestamp: float = Field(description='''Update timestamp (milliseconds)''')
  channel: str = Field(description='''Channel pattern for wallet position risk''')
  account_id: int = Field(description='''The account this frame covers. Its rows replace all of the account's previous
  rows; an empty list means the account has no open positions.
  ''', alias='''accountId''')
  data: List[PositionRisk] = Field()
