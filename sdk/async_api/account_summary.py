from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field

class AccountSummary(BaseModel): 
  account_id: int = Field(description='''Perpetual margin account ID.''', alias='''accountId''')
  collateral_value: str = Field(description='''Realized collateral value after haircuts, before unrealized position PnL. Positive collateral is discounted; a debt is increased by the inverse haircut.''', alias='''collateralValue''')
  unrealized_pnl: str = Field(description='''Sum of unrealized position PnL at mark prices. Funding and ADL cashflows already included in realized balances are not added again.''', alias='''unrealizedPnl''')
  margin_balance: str = Field(description='''collateralValue + unrealizedPnl. The value available to cover margin requirements, after collateral haircuts.''', alias='''marginBalance''')
  equity: str = Field(description='''Account value before collateral haircuts: each realized collateral balance at its oracle price, a debt included at its price, each rounded down to rUSD precision, + unrealizedPnl. collateralValue applies haircuts in the account's disfavour, discounting a holding to price × (1 − haircut) and charging a debt at price / (1 − haircut), so equity is never below marginBalance, for holdings and debts alike, and equals it for an account holding only rUSD. Margin requirements are measured against marginBalance, not equity.''')
  initial_margin_requirement: str = Field(description='''Initial margin required by settled positions, at the leverage the account uses on each market (see GET /wallet/{address}/leverages).''', alias='''initialMarginRequirement''')
  liquidation_margin_requirement: str = Field(description='''Liquidation margin required by settled positions; the account is liquidatable below it.''', alias='''liquidationMarginRequirement''')
  free_collateral: str = Field(description='''marginBalance - initialMarginRequirement. Negative means an initial-margin deficit. Not an order-size quote or withdrawal limit.''', alias='''freeCollateral''')
  initial_margin_ratio: Optional[str] = Field(description='''initialMarginRequirement / marginBalance as a decimal fraction (1 means 100%). Rounded up to at most 18 decimal places. Null when marginBalance is zero or negative.''', alias='''initialMarginRatio''')
  liquidation_margin_ratio: Optional[str] = Field(description='''liquidationMarginRequirement / marginBalance as a decimal fraction. Rounded up to at most 18 decimal places. Null when marginBalance is zero or negative.''', alias='''liquidationMarginRatio''')
  updated_at: int = Field(description='''Calculation time, in Unix milliseconds.''', alias='''updatedAt''')
  prices_updated_at: Optional[int] = Field(description='''Source time of the oldest mark or collateral price used by the account, in Unix milliseconds: the matching engine's timestamp for a mark, the oracle's for a collateral price. Null when no price is needed.''', alias='''pricesUpdatedAt''')
  additional_properties: Optional[dict[str, Any]] = Field(default=None, exclude=True)

  @model_serializer(mode='wrap')
  def custom_serializer(self, handler):
    serialized_self = handler(self)
    additional_properties = getattr(self, "additional_properties")
    if additional_properties is not None:
      for key, value in additional_properties.items():
        # Never overwrite existing values, to avoid clashes
        if not key in serialized_self:
          serialized_self[key] = value

    return serialized_self

  @model_validator(mode='before')
  @classmethod
  def unwrap_additional_properties(cls, data):
    if not isinstance(data, dict):
      data = data.model_dump()
    json_properties = list(data.keys())
    known_object_properties = ['account_id', 'collateral_value', 'unrealized_pnl', 'margin_balance', 'equity', 'initial_margin_requirement', 'liquidation_margin_requirement', 'free_collateral', 'initial_margin_ratio', 'liquidation_margin_ratio', 'updated_at', 'prices_updated_at', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['accountId', 'collateralValue', 'unrealizedPnl', 'marginBalance', 'equity', 'initialMarginRequirement', 'liquidationMarginRequirement', 'freeCollateral', 'initialMarginRatio', 'liquidationMarginRatio', 'updatedAt', 'pricesUpdatedAt', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

