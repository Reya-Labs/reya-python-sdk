from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema81 import AnonymousSchema81
class ExecutionBustReasonStalePriceDetected(BaseModel): 
  reason_name: AnonymousSchema81 = Field(alias='''reasonName''')
  node_id: str = Field(description='''Oracle node identifier that returned a stale price.''', alias='''nodeId''')
