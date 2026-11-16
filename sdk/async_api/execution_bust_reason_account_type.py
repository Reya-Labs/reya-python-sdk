from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema158 import AnonymousSchema158
class ExecutionBustReasonAccountType(BaseModel): 
  reason_name: AnonymousSchema158 = Field(alias='''reasonName''')
  account_id: int = Field(description='''Account identifier whose account type failed the check.''', alias='''accountId''')
