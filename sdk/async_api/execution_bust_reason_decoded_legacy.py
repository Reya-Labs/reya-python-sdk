from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema150 import AnonymousSchema150
class ExecutionBustReasonDecodedLegacy(BaseModel): 
  reason_name: AnonymousSchema150 = Field(alias='''reasonName''')
  message: str = Field(description='''Already-decoded legacy string preserved as a message.''')
