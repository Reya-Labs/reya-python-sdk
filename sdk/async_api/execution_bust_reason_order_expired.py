from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema101 import AnonymousSchema101
class ExecutionBustReasonOrderExpired(BaseModel): 
  reason_name: AnonymousSchema101 = Field(alias='''reasonName''')
  expired_at: int = Field(description='''Order expiration timestamp.''', alias='''expiredAt''')
