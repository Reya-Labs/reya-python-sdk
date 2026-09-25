from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema78 import AnonymousSchema78
class ExecutionBustReasonMarkPriceStale(BaseModel): 
  reason_name: AnonymousSchema78 = Field(alias='''reasonName''')
  market_id: int = Field(description='''Market identifier whose mark price is stale.''', alias='''marketId''')
  price_timestamp: int = Field(description='''Timestamp carried by the price update.''', alias='''priceTimestamp''')
  block_timestamp: int = Field(description='''Block timestamp observed by the contracts.''', alias='''blockTimestamp''')
  max_stale_duration: int = Field(description='''Maximum allowed price staleness in seconds.''', alias='''maxStaleDuration''')
