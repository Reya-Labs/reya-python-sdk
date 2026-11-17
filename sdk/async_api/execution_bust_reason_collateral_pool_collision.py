from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema153 import AnonymousSchema153
class ExecutionBustReasonCollateralPoolCollision(BaseModel): 
  reason_name: AnonymousSchema153 = Field(alias='''reasonName''')
  collateral_pool_id: int = Field(description='''Account collateral pool identifier.''', alias='''collateralPoolId''')
  counterparty_collateral_pool_id: int = Field(description='''Counterparty collateral pool identifier.''', alias='''counterpartyCollateralPoolId''')
