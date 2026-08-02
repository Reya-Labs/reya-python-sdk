from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema92 import AnonymousSchema92
class ExecutionBustReasonMaxQuantityExceeded(BaseModel): 
  reason_name: AnonymousSchema92 = Field(alias='''reasonName''')
  updated_quantity: str = Field(description='''Updated nonce quantity.''', alias='''updatedQuantity''')
  max_quantity: str = Field(description='''Maximum allowed nonce quantity.''', alias='''maxQuantity''')
