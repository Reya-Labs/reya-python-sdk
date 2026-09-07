from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema67 import AnonymousSchema67
class ExecutionBustReasonMarketNotFound(BaseModel): 
  reason_name: AnonymousSchema67 = Field(alias='''reasonName''')
  market_id: int = Field(description='''Market identifier that does not exist.''', alias='''marketId''')
