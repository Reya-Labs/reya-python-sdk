from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema71 import AnonymousSchema71
class ExecutionBustReasonUnauthorizedSigner(BaseModel): 
  reason_name: AnonymousSchema71 = Field(alias='''reasonName''')
  signer: str = Field(description='''Signer address that is not authorized to trade for the account.''')
