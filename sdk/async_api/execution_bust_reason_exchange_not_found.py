from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema65 import AnonymousSchema65
class ExecutionBustReasonExchangeNotFound(BaseModel): 
  reason_name: AnonymousSchema65 = Field(alias='''reasonName''')
  exchange_id: int = Field(description='''Exchange identifier that does not exist.''', alias='''exchangeId''')
