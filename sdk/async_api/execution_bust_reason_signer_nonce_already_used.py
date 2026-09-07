from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema75 import AnonymousSchema75
class ExecutionBustReasonSignerNonceAlreadyUsed(BaseModel): 
  reason_name: AnonymousSchema75 = Field(alias='''reasonName''')
  signer: str = Field(description='''Signer address that already used the nonce.''')
  nonce: str = Field(description='''Signer nonce that was already consumed. Kept as a string because contract nonces are uint256.''')
