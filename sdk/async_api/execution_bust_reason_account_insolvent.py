from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema163 import AnonymousSchema163
class ExecutionBustReasonAccountInsolvent(BaseModel): 
  reason_name: AnonymousSchema163 = Field(alias='''reasonName''')
  account_id: int = Field(description='''Account identifier that is insolvent.''', alias='''accountId''')
  margin_balance: str = Field(description='''Margin balance decoded from an 18-decimal fixed-point contract value.''', alias='''marginBalance''')
