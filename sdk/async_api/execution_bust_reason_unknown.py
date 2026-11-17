from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema170 import AnonymousSchema170
class ExecutionBustReasonUnknown(BaseModel): 
  reason_name: AnonymousSchema170 = Field(alias='''reasonName''')
  message: str = Field(description='''Support-facing fallback for undecodable or empty revert bytes.''')
