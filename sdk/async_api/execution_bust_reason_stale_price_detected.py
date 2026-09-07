from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema92 import AnonymousSchema92
class ExecutionBustReasonStalePriceDetected(BaseModel): 
  reason_name: AnonymousSchema92 = Field(alias='''reasonName''')
  node_id: str = Field(description='''Oracle node identifier that returned a stale price.''', alias='''nodeId''')
