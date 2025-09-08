from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field

class PongMessage(BaseModel): 
  type: str = Field(description='''Message type identifier''', default='pong', frozen=True)
  timestamp: Optional[float] = Field(description='''Client timestamp (milliseconds)''', default=None)
