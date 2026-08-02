from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema101 import AnonymousSchema101
class ExecutionBustReasonBaseDeltaWrongSpacing(BaseModel): 
  reason_name: AnonymousSchema101 = Field(alias='''reasonName''')
  base_delta: str = Field(description='''Base delta decoded from an 18-decimal fixed-point contract value.''', alias='''baseDelta''')
  base_spacing: str = Field(description='''Base spacing decoded from an 18-decimal fixed-point contract value.''', alias='''baseSpacing''')
