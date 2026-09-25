from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.subscribed_message_type import SubscribedMessageType
from sdk.async_api.order_changes_snapshot import OrderChangesSnapshot
class OrderChangesSubscribedPayload(BaseModel): 
  type: SubscribedMessageType = Field(description='''Message type for subscribed confirmation messages''')
  channel: str = Field(description='''Channel pattern for wallet order changes''')
  contents: OrderChangesSnapshot = Field(description='''Initial orderChanges subscription payload: current open orders and the sequence cursor that separates this snapshot from subsequent updates. See the walletOrderChanges channel for stream handling.''')
