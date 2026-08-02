from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema56 import AnonymousSchema56
class ExecutionBustReasonMarketNotFound(BaseModel): 
  reason_name: AnonymousSchema56 = Field(alias='''reasonName''')
  market_id: int = Field(description='''Market identifier that does not exist.''', alias='''marketId''')
