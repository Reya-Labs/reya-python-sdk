from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema125 import AnonymousSchema125
class ExecutionBustReasonFeatureUnavailable(BaseModel): 
  reason_name: AnonymousSchema125 = Field(alias='''reasonName''')
  feature: str = Field(description='''Feature identifier that is disabled.''')
