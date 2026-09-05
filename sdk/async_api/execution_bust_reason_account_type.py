from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema140 import AnonymousSchema140
class ExecutionBustReasonAccountType(BaseModel): 
  reason_name: AnonymousSchema140 = Field(alias='''reasonName''')
  account_id: int = Field(description='''Account identifier whose account type failed the check.''', alias='''accountId''')
