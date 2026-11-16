from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema119 import AnonymousSchema119
class ExecutionBustReasonOrderExpired(BaseModel): 
  reason_name: AnonymousSchema119 = Field(alias='''reasonName''')
  expired_at: int = Field(description='''Order expiration timestamp.''', alias='''expiredAt''')
