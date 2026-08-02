from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema98 import AnonymousSchema98
class ExecutionBustReasonMarketIdMismatch(BaseModel): 
  reason_name: AnonymousSchema98 = Field(alias='''reasonName''')
  account_market_id: int = Field(description='''Account-side market identifier.''', alias='''accountMarketId''')
  counterparty_market_id: int = Field(description='''Counterparty-side market identifier.''', alias='''counterpartyMarketId''')
