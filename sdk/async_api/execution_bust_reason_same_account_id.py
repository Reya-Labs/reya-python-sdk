from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema148 import AnonymousSchema148
class ExecutionBustReasonSameAccountId(BaseModel): 
  reason_name: AnonymousSchema148 = Field(alias='''reasonName''')
  account_id: Optional[int] = Field(description='''Account identifier that matched itself, when emitted by the contract.''', default=None, alias='''accountId''')
