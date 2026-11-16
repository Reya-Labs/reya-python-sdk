from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema121 import AnonymousSchema121
class ExecutionBustReasonMaxQuantityExceeded(BaseModel): 
  reason_name: AnonymousSchema121 = Field(alias='''reasonName''')
  updated_quantity: str = Field(description='''Updated nonce quantity.''', alias='''updatedQuantity''')
  max_quantity: str = Field(description='''Maximum allowed nonce quantity.''', alias='''maxQuantity''')
