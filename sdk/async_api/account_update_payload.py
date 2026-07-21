from __future__ import annotations
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.channel_data_message_type import ChannelDataMessageType
from sdk.async_api.account_update_data import AccountUpdateData
class AccountUpdatePayload(BaseModel): 
  type: ChannelDataMessageType = Field(description='''Message type for channel data updates''')
  timestamp: float = Field(description='''Update timestamp (milliseconds)''')
  channel: str = Field(description='''Channel pattern for wallet account creation and binding updates''')
  data: List[AccountUpdateData] = Field(description='''The account whose creation or owner binding changed.''')
