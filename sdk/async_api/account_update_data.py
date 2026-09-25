from __future__ import annotations
from typing import Dict, Optional, Any
from pydantic import BaseModel, ConfigDict, Field

class AccountUpdateData(BaseModel):
  model_config = ConfigDict(extra="forbid")
  account_id: str = Field(pattern=r'^\d+$', description='''Decimal-string account identifier whose creation or binding changed.''', alias='''accountId''')
  owner: str = Field(pattern=r'^0x[a-fA-F0-9]{40}$')
  main_account_id: Optional[str] = Field(pattern=r'^\d+$', description='''Decimal-string main perp account identifier for the owner, or null when none is bound.''', alias='''mainAccountId''')
  spot_account_id: Optional[str] = Field(pattern=r'^\d+$', description='''Decimal-string spot account identifier for the owner, or null when none is bound.''', alias='''spotAccountId''')
  is_main_perp_account: bool = Field(description='''Whether accountId is currently the owner's main perp account.''', alias='''isMainPerpAccount''')
  is_spot_account: bool = Field(description='''Whether accountId is currently the owner's spot account.''', alias='''isSpotAccount''')
  removed: Optional[bool] = Field(description='''Present and true only on the previous owner's channel during a defensive ownership rebind notification.''', default=None)
