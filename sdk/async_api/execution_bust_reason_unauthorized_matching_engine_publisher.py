from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema91 import AnonymousSchema91
class ExecutionBustReasonUnauthorizedMatchingEnginePublisher(BaseModel): 
  reason_name: AnonymousSchema91 = Field(alias='''reasonName''')
  publisher: str = Field(description='''Address associated with the publisher authorization failure.''')
