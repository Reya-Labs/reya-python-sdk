from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema116 import AnonymousSchema116
class ExecutionBustReasonReduceOnlyConditionFailed(BaseModel): 
  reason_name: AnonymousSchema116 = Field(alias='''reasonName''')
  market_id: int = Field(description='''Market identifier for the reduce-only check.''', alias='''marketId''')
  account_id: int = Field(description='''Account identifier for the reduce-only check.''', alias='''accountId''')
