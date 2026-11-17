from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_api.side import Side
class PositionRisk(BaseModel): 
  account_id: int = Field(description='''Perpetual margin account ID.''', alias='''accountId''')
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''')
  side: Side = Field(description='''Order side (B = Buy/Bid, A = Ask/Sell)''')
  qty: str = Field(description='''Open quantity, as in GET /wallet/{address}/positions.''')
  avg_entry_price: str = Field(alias='''avgEntryPrice''')
  last_trade_sequence_number: int = Field(alias='''lastTradeSequenceNumber''')
  mark_price: str = Field(description='''Mark price used to value this position.''', alias='''markPrice''')
  notional: str = Field(description='''qty × markPrice, in rUSD.''')
  unrealized_pnl: str = Field(description='''Position PnL at markPrice, in rUSD. Realized funding and ADL cashflows are already in the account balance.''', alias='''unrealizedPnl''')
  funding_since_change: str = Field(description='''Funding on the position since its size last changed (a fill, a liquidation or an ADL), in rUSD; positive means the position received funding. Resets to zero at the next change. Already included in collateralValue and marginBalance: do not add it to them.''', alias='''fundingSinceChange''')
  funding_since_open: str = Field(description='''Funding on the position since it opened, in rUSD; positive means the position received funding. A position opens from flat or when it flips to the other side; increases and partial closes keep the running total. Includes fundingSinceChange and updates live. Already included in collateralValue and marginBalance: do not add it to them.''', alias='''fundingSinceOpen''')
  liquidation_margin_requirement: str = Field(description='''This position's liquidation margin requirement, in rUSD.''', alias='''liquidationMarginRequirement''')
  initial_margin_requirement: str = Field(description='''This position's initial margin requirement, in rUSD: what it needs at its leverage. The stored value is read back at the market's current imMultiplier, so after governance changes it this may no longer equal notional ÷ leverage. Rounded up per position, so a wallet's positions can sum to slightly more than the account's initialMarginRequirement, which is rounded once.''', alias='''initialMarginRequirement''')
  leverage: int = Field(description='''Leverage the account's initial margin uses on this market: the account's setting from GET /wallet/{address}/leverages, capped at the market's maxLeverage, or the market's maxLeverage when the account has no setting. The stored value is read back at the market's current imMultiplier, so after governance changes the multiplier this can differ from the leverage that was set.''')
  liquidation_price: Optional[str] = Field(description='''Estimated cross-margin liquidation price, holding other prices fixed and rounded to the price tick toward the mark. Null when no positive price is a liquidation boundary.''', alias='''liquidationPrice''')
  has_correlated_collateral: bool = Field(description='''True when the account holds non-stable collateral that tracks this market's underlying, so the fixed-collateral-price estimate is less reliable.''', alias='''hasCorrelatedCollateral''')
  updated_at: int = Field(description='''Calculation time, in Unix milliseconds.''', alias='''updatedAt''')
  prices_updated_at: int = Field(description='''Source time of the oldest mark or collateral price used to value the account, in Unix milliseconds: the matching engine's timestamp for a mark, the oracle's for a collateral price. Every row of the account carries the same value.''', alias='''pricesUpdatedAt''')
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
    known_object_properties = ['account_id', 'symbol', 'side', 'qty', 'avg_entry_price', 'last_trade_sequence_number', 'mark_price', 'notional', 'unrealized_pnl', 'funding_since_change', 'funding_since_open', 'liquidation_margin_requirement', 'initial_margin_requirement', 'leverage', 'liquidation_price', 'has_correlated_collateral', 'updated_at', 'prices_updated_at', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['accountId', 'symbol', 'side', 'qty', 'avgEntryPrice', 'lastTradeSequenceNumber', 'markPrice', 'notional', 'unrealizedPnl', 'fundingSinceChange', 'fundingSinceOpen', 'liquidationMarginRequirement', 'initialMarginRequirement', 'leverage', 'liquidationPrice', 'hasCorrelatedCollateral', 'updatedAt', 'pricesUpdatedAt', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

