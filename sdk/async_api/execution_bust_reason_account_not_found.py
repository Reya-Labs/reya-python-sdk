from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema81 import AnonymousSchema81
class ExecutionBustReasonAccountNotFound(BaseModel): 
  reason_name: AnonymousSchema81 = Field(alias='''reasonName''')
  account_id: int = Field(description='''Account identifier that does not exist.''', alias='''accountId''')
