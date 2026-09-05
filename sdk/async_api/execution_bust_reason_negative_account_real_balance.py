from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema142 import AnonymousSchema142
class ExecutionBustReasonNegativeAccountRealBalance(BaseModel): 
  reason_name: AnonymousSchema142 = Field(alias='''reasonName''')
  account_id: int = Field(description='''Account identifier with negative real balance.''', alias='''accountId''')
  real_balance: str = Field(description='''Real balance decoded from an 18-decimal fixed-point contract value.''', alias='''realBalance''')
