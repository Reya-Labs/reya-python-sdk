from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_exec_api.disconnect_notice_message_type import DisconnectNoticeMessageType
from sdk.async_exec_api.disconnect_notice_reason import DisconnectNoticeReason
class DisconnectNoticeMessagePayload(BaseModel): 
  type: DisconnectNoticeMessageType = Field(description='''Message type for disconnect notices''')
  reason: DisconnectNoticeReason = Field(description='''Why the connection will be closed.''')
  close_in_ms: int = Field(description='''How long requests are normally still processed, in milliseconds after the server sent this message (`timestamp`), not after you receive it. Allow for network delay.''', alias='''closeInMs''')
  timestamp: int = Field(description='''When the server sent the notice, in milliseconds since the Unix epoch.''')
