from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema138 import AnonymousSchema138
class ExecutionBustReasonOpenInterestExceeded(BaseModel): 
  reason_name: AnonymousSchema138 = Field(alias='''reasonName''')
  market_id: int = Field(description='''Market identifier whose open-interest cap was exceeded.''', alias='''marketId''')
