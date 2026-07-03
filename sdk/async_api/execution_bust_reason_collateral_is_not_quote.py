from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema116 import AnonymousSchema116
class ExecutionBustReasonCollateralIsNotQuote(BaseModel): 
  reason_name: AnonymousSchema116 = Field(alias='''reasonName''')
  collateral_pool_id: int = Field(description='''Collateral pool identifier.''', alias='''collateralPoolId''')
  collateral: str = Field(description='''Collateral address that is not the quote collateral.''')
