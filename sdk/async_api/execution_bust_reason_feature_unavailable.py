from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema143 import AnonymousSchema143
class ExecutionBustReasonFeatureUnavailable(BaseModel): 
  reason_name: AnonymousSchema143 = Field(alias='''reasonName''')
  feature: str = Field(description='''Feature identifier that is disabled.''')
