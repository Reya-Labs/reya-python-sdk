from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema106 import AnonymousSchema106
class ExecutionBustReasonMarketTypeMismatch(BaseModel): 
  reason_name: AnonymousSchema106 = Field(alias='''reasonName''')
  account_market_type: int = Field(description='''Account-side market type.''', alias='''accountMarketType''')
  counterparty_market_type: int = Field(description='''Counterparty-side market type.''', alias='''counterpartyMarketType''')
