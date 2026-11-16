from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema124 import AnonymousSchema124
class ExecutionBustReasonMarketTypeMismatch(BaseModel): 
  reason_name: AnonymousSchema124 = Field(alias='''reasonName''')
  account_market_type: int = Field(description='''Account-side market type.''', alias='''accountMarketType''')
  counterparty_market_type: int = Field(description='''Counterparty-side market type.''', alias='''counterpartyMarketType''')
