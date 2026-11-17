from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema168 import AnonymousSchema168
class ExecutionBustReasonDecodedLegacy(BaseModel): 
  reason_name: AnonymousSchema168 = Field(alias='''reasonName''')
  message: str = Field(description='''Already-decoded legacy string preserved as a message.''')
