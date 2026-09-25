from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema69 import AnonymousSchema69
class ExecutionBustReasonUnauthorized(BaseModel): 
  reason_name: AnonymousSchema69 = Field(alias='''reasonName''')
  address: str = Field(description='''Address that is not authorized.''')
