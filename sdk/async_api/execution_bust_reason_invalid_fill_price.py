from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema107 import AnonymousSchema107
class ExecutionBustReasonInvalidFillPrice(BaseModel): 
  reason_name: AnonymousSchema107 = Field(alias='''reasonName''')
  fill_price: str = Field(description='''Fill price decoded from an 18-decimal fixed-point contract value.''', alias='''fillPrice''')
  order_price: str = Field(description='''Order limit price decoded from an 18-decimal fixed-point contract value.''', alias='''orderPrice''')
