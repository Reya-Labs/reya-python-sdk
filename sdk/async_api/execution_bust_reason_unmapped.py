from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field

class ExecutionBustReasonUnmapped(BaseModel): 
  reason_name: str = Field(description='''Decoded custom-error name not explicitly modeled by this schema.''', alias='''reasonName''')
  args: dict[str, str] = Field(description='''Named ABI inputs, or arg0/arg1 fallback names, encoded as strings to avoid precision loss.''')
