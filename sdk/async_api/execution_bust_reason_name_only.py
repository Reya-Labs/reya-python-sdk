from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema62 import AnonymousSchema62
class ExecutionBustReasonNameOnly(BaseModel): 
  reason_name: AnonymousSchema62 = Field(alias='''reasonName''')
