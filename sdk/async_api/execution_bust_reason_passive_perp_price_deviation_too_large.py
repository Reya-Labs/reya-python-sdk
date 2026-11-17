from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema101 import AnonymousSchema101
class ExecutionBustReasonPassivePerpPriceDeviationTooLarge(BaseModel): 
  reason_name: AnonymousSchema101 = Field(alias='''reasonName''')
  market_id: int = Field(description='''Market identifier whose fill price deviated too far.''', alias='''marketId''')
  price: str = Field(description='''Fill price decoded from an 18-decimal fixed-point contract value.''')
  reference_price: str = Field(description='''Reference price decoded from an 18-decimal fixed-point contract value.''', alias='''referencePrice''')
  max_deviation: str = Field(description='''Maximum allowed price deviation decoded from an 18-decimal fixed-point contract value.''', alias='''maxDeviation''')
