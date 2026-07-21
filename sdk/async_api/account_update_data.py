from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field

class AccountUpdateData(BaseModel): 
  account_id: str = Field(description='''Decimal-string account identifier whose creation or binding changed.''', alias='''accountId''')
  owner: str = Field()
  main_account_id: Optional[str] = Field(description='''Decimal-string main perp account identifier for the owner, or null when none is bound.''', default=None, alias='''mainAccountId''')
  spot_account_id: Optional[str] = Field(description='''Decimal-string spot account identifier for the owner, or null when none is bound.''', default=None, alias='''spotAccountId''')
  is_main_perp_account: bool = Field(description='''Whether accountId is currently the owner's main perp account.''', alias='''isMainPerpAccount''')
  is_spot_account: bool = Field(description='''Whether accountId is currently the owner's spot account.''', alias='''isSpotAccount''')
  removed: Optional[bool] = Field(description='''Present and true only on the previous owner's channel during a defensive ownership rebind notification.''', default=None)
