from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema98 import AnonymousSchema98
class ExecutionBustReasonReduceOnlyConditionFailed(BaseModel): 
  reason_name: AnonymousSchema98 = Field(alias='''reasonName''')
  market_id: int = Field(description='''Market identifier for the reduce-only check.''', alias='''marketId''')
  account_id: int = Field(description='''Account identifier for the reduce-only check.''', alias='''accountId''')
