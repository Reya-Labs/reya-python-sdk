from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.subscribed_message_type import SubscribedMessageType
from sdk.async_api.order_changes_snapshot import OrderChangesSnapshot
class OrderChangesSubscribedPayload(BaseModel): 
  type: SubscribedMessageType = Field(description='''Message type for subscribed confirmation messages''')
  channel: str = Field(description='''Channel pattern for wallet order changes''')
  contents: OrderChangesSnapshot = Field(description='''Initial payload returned in the `subscribed` confirmation for the `/v2/wallet/{address}/orderChanges` WS channel. Combines the current open-orders snapshot with a `snapshotSequenceNumber` cursor; subsequent orderChanges WS messages carry orders with a greater `sequenceNumber`, so clients can splice the live feed against the orderHistory REST endpoint.''')
