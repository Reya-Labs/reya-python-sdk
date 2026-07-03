from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema131 import AnonymousSchema131
class ExecutionBustReasonNegativeAccountRealBalance(BaseModel): 
  reason_name: AnonymousSchema131 = Field(alias='''reasonName''')
  account_id: int = Field(description='''Account identifier with negative real balance.''', alias='''accountId''')
  real_balance: str = Field(description='''Real balance decoded from an 18-decimal fixed-point contract value.''', alias='''realBalance''')
