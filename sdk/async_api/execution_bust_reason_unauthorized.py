from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema58 import AnonymousSchema58
class ExecutionBustReasonUnauthorized(BaseModel): 
  reason_name: AnonymousSchema58 = Field(alias='''reasonName''')
  address: str = Field(description='''Address that is not authorized.''')
