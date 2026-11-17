from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema85 import AnonymousSchema85
class ExecutionBustReasonMarketNotFound(BaseModel): 
  reason_name: AnonymousSchema85 = Field(alias='''reasonName''')
  market_id: int = Field(description='''Market identifier that does not exist.''', alias='''marketId''')
