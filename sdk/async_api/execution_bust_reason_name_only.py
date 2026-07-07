from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema51 import AnonymousSchema51
class ExecutionBustReasonNameOnly(BaseModel): 
  reason_name: AnonymousSchema51 = Field(alias='''reasonName''')
