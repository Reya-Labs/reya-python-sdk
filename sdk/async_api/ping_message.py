from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field

class PingMessage(BaseModel): 
  type: str = Field(description='''Message type identifier''', default='ping', frozen=True)
  timestamp: Optional[float] = Field(description='''Server timestamp (milliseconds)''', default=None)
