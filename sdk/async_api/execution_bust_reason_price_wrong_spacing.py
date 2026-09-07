from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema115 import AnonymousSchema115
class ExecutionBustReasonPriceWrongSpacing(BaseModel): 
  reason_name: AnonymousSchema115 = Field(alias='''reasonName''')
  price: str = Field(description='''Price decoded from an 18-decimal fixed-point contract value.''')
  price_spacing: str = Field(description='''Price spacing decoded from an 18-decimal fixed-point contract value.''', alias='''priceSpacing''')
