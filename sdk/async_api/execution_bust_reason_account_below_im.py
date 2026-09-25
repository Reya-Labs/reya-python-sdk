from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema94 import AnonymousSchema94
class ExecutionBustReasonAccountBelowIm(BaseModel): 
  reason_name: AnonymousSchema94 = Field(alias='''reasonName''')
  account_id: int = Field(description='''Account identifier that is below initial margin.''', alias='''accountId''')
  delta: str = Field(description='''Signed initial-margin delta decoded from an 18-decimal fixed-point contract value.''')
  shortfall: str = Field(description='''Absolute initial-margin shortfall decoded from an 18-decimal fixed-point contract value.''')
