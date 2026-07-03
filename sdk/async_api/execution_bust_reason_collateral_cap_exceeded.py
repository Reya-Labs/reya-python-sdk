from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema119 import AnonymousSchema119
class ExecutionBustReasonCollateralCapExceeded(BaseModel): 
  reason_name: AnonymousSchema119 = Field(alias='''reasonName''')
  collateral_pool_id: int = Field(description='''Collateral pool identifier.''', alias='''collateralPoolId''')
  collateral: str = Field(description='''Collateral address whose pool cap was exceeded.''')
  collateral_cap: str = Field(description='''Collateral cap.''', alias='''collateralCap''')
  collateral_balance: str = Field(description='''Collateral balance.''', alias='''collateralBalance''')
