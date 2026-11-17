from __future__ import annotations
from typing import Any, List, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_api.candle import Candle
class MarketCandlesSnapshot(BaseModel): 
  data: List[Candle] = Field(description='''The newest candle, or empty''')
