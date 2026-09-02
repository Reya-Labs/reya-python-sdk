from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema152 import AnonymousSchema152
class ExecutionBustReasonUnknown(BaseModel): 
  reason_name: AnonymousSchema152 = Field(alias='''reasonName''')
  message: str = Field(description='''Support-facing fallback for undecodable or empty revert bytes.''')
