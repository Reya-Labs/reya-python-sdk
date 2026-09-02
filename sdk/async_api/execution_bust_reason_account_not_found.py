from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema63 import AnonymousSchema63
class ExecutionBustReasonAccountNotFound(BaseModel): 
  reason_name: AnonymousSchema63 = Field(alias='''reasonName''')
  account_id: int = Field(description='''Account identifier that does not exist.''', alias='''accountId''')
