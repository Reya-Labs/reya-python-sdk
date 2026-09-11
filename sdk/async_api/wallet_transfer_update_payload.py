from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.channel_data_message_type import ChannelDataMessageType
from sdk.async_api.transfer import Transfer
class WalletTransferUpdatePayload(BaseModel): 
  type: ChannelDataMessageType = Field(description='''Message type for channel data updates''')
  timestamp: float = Field(description='''Update timestamp (milliseconds)''')
  channel: str = Field(description='''Channel pattern for wallet transfer history''')
  data: List[Transfer] = Field(description='''Transfer entries carried by this live update. See the walletTransfers channel for snapshot and live-frame delivery.
  ''')
