from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema62 import AnonymousSchema62
class ExecutionBustReasonUnauthorizedMatchingEnginePublisher(BaseModel): 
  reason_name: AnonymousSchema62 = Field(alias='''reasonName''')
  publisher: str = Field(description='''Matching-engine publisher address that is not authorized.''')
