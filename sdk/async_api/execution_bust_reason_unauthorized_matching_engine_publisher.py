from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema73 import AnonymousSchema73
class ExecutionBustReasonUnauthorizedMatchingEnginePublisher(BaseModel): 
  reason_name: AnonymousSchema73 = Field(alias='''reasonName''')
  publisher: str = Field(description='''Matching-engine publisher address that is not authorized.''')
