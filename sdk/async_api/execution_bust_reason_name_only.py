from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema80 import AnonymousSchema80
class ExecutionBustReasonNameOnly(BaseModel): 
  reason_name: AnonymousSchema80 = Field(alias='''reasonName''')
