from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema127 import AnonymousSchema127
class ExecutionBustReasonOpenInterestExceeded(BaseModel): 
  reason_name: AnonymousSchema127 = Field(alias='''reasonName''')
  market_id: int = Field(description='''Market identifier whose open-interest cap was exceeded.''', alias='''marketId''')
