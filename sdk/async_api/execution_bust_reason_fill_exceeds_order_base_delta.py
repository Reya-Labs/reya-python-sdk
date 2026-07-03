from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from sdk.async_api.anonymous_schema110 import AnonymousSchema110
from sdk.async_api.anonymous_schema113 import AnonymousSchema113
class ExecutionBustReasonFillExceedsOrderBaseDelta(BaseModel): 
  reason_name: AnonymousSchema110 = Field(alias='''reasonName''')
  fill_amount: str = Field(description='''Fill amount decoded from an 18-decimal fixed-point contract value.''', alias='''fillAmount''')
  order_base_delta: str = Field(description='''Order base delta decoded from an 18-decimal fixed-point contract value.''', alias='''orderBaseDelta''')
  order_role: AnonymousSchema113 = Field(description='''Whether the failed order was the account or counterparty order.''', alias='''orderRole''')
