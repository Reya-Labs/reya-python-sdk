from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema106 import AnonymousSchema106
class ExecutionBustReasonSpotPriceDeviationTooLarge(BaseModel): 
  reason_name: AnonymousSchema106 = Field(alias='''reasonName''')
  fill_price: str = Field(description='''Fill price decoded from an 18-decimal fixed-point contract value.''', alias='''fillPrice''')
  oracle_price: str = Field(description='''Oracle price decoded from an 18-decimal fixed-point contract value.''', alias='''oraclePrice''')
  oracle_deviation: str = Field(description='''Oracle deviation threshold decoded from an 18-decimal fixed-point contract value.''', alias='''oracleDeviation''')
