from __future__ import annotations
from typing import Union, Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_api.side import Side
from sdk.async_api.execution_bust_reason_name_only import ExecutionBustReasonNameOnly
from sdk.async_api.execution_bust_reason_account_not_found import ExecutionBustReasonAccountNotFound
from sdk.async_api.execution_bust_reason_exchange_not_found import ExecutionBustReasonExchangeNotFound
from sdk.async_api.execution_bust_reason_market_not_found import ExecutionBustReasonMarketNotFound
from sdk.async_api.execution_bust_reason_unauthorized import ExecutionBustReasonUnauthorized
from sdk.async_api.execution_bust_reason_unauthorized_signer import ExecutionBustReasonUnauthorizedSigner
from sdk.async_api.execution_bust_reason_unauthorized_matching_engine_publisher import ExecutionBustReasonUnauthorizedMatchingEnginePublisher
from sdk.async_api.execution_bust_reason_signer_nonce_already_used import ExecutionBustReasonSignerNonceAlreadyUsed
from sdk.async_api.execution_bust_reason_mark_price_stale import ExecutionBustReasonMarkPriceStale
from sdk.async_api.execution_bust_reason_passive_perp_price_deviation_too_large import ExecutionBustReasonPassivePerpPriceDeviationTooLarge
from sdk.async_api.execution_bust_reason_spot_price_deviation_too_large import ExecutionBustReasonSpotPriceDeviationTooLarge
from sdk.async_api.execution_bust_reason_stale_price_detected import ExecutionBustReasonStalePriceDetected
from sdk.async_api.execution_bust_reason_account_below_im import ExecutionBustReasonAccountBelowIm
from sdk.async_api.execution_bust_reason_reduce_only_condition_failed import ExecutionBustReasonReduceOnlyConditionFailed
from sdk.async_api.execution_bust_reason_order_expired import ExecutionBustReasonOrderExpired
from sdk.async_api.execution_bust_reason_max_quantity_exceeded import ExecutionBustReasonMaxQuantityExceeded
from sdk.async_api.execution_bust_reason_market_type_mismatch import ExecutionBustReasonMarketTypeMismatch
from sdk.async_api.execution_bust_reason_market_id_mismatch import ExecutionBustReasonMarketIdMismatch
from sdk.async_api.execution_bust_reason_base_delta_wrong_spacing import ExecutionBustReasonBaseDeltaWrongSpacing
from sdk.async_api.execution_bust_reason_price_wrong_spacing import ExecutionBustReasonPriceWrongSpacing
from sdk.async_api.execution_bust_reason_invalid_fill_price import ExecutionBustReasonInvalidFillPrice
from sdk.async_api.execution_bust_reason_fill_exceeds_order_base_delta import ExecutionBustReasonFillExceedsOrderBaseDelta
from sdk.async_api.execution_bust_reason_feature_unavailable import ExecutionBustReasonFeatureUnavailable
from sdk.async_api.execution_bust_reason_collateral_is_not_quote import ExecutionBustReasonCollateralIsNotQuote
from sdk.async_api.execution_bust_reason_collateral_cap_exceeded import ExecutionBustReasonCollateralCapExceeded
from sdk.async_api.execution_bust_reason_collateral_pool_collision import ExecutionBustReasonCollateralPoolCollision
from sdk.async_api.execution_bust_reason_open_interest_exceeded import ExecutionBustReasonOpenInterestExceeded
from sdk.async_api.execution_bust_reason_account_type import ExecutionBustReasonAccountType
from sdk.async_api.execution_bust_reason_negative_account_real_balance import ExecutionBustReasonNegativeAccountRealBalance
from sdk.async_api.execution_bust_reason_account_insolvent import ExecutionBustReasonAccountInsolvent
from sdk.async_api.execution_bust_reason_same_account_id import ExecutionBustReasonSameAccountId
from sdk.async_api.execution_bust_reason_decoded_legacy import ExecutionBustReasonDecodedLegacy
from sdk.async_api.execution_bust_reason_unknown import ExecutionBustReasonUnknown
from sdk.async_api.execution_bust_reason_unmapped import ExecutionBustReasonUnmapped
class ExecutionBust(BaseModel):
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''')
  taker_account_id: int = Field(alias='''takerAccountId''')
  exchange_id: int = Field(alias='''exchangeId''')
  maker_account_id: int = Field(alias='''makerAccountId''')
  taker_order_id: str = Field(description='''Taker order ID''', alias='''takerOrderId''')
  maker_order_id: str = Field(description='''Order ID for the maker''', alias='''makerOrderId''')
  qty: str = Field()
  side: Side = Field(description='''Order side (B = Buy/Bid, A = Ask/Sell)''')
  price: str = Field()
  reason: Union[ExecutionBustReasonNameOnly, ExecutionBustReasonAccountNotFound, ExecutionBustReasonExchangeNotFound, ExecutionBustReasonMarketNotFound, ExecutionBustReasonUnauthorized, ExecutionBustReasonUnauthorizedSigner, ExecutionBustReasonUnauthorizedMatchingEnginePublisher, ExecutionBustReasonSignerNonceAlreadyUsed, ExecutionBustReasonMarkPriceStale, Union[ExecutionBustReasonPassivePerpPriceDeviationTooLarge, ExecutionBustReasonSpotPriceDeviationTooLarge], ExecutionBustReasonStalePriceDetected, ExecutionBustReasonAccountBelowIm, ExecutionBustReasonReduceOnlyConditionFailed, ExecutionBustReasonOrderExpired, ExecutionBustReasonMaxQuantityExceeded, ExecutionBustReasonMarketTypeMismatch, ExecutionBustReasonMarketIdMismatch, ExecutionBustReasonBaseDeltaWrongSpacing, ExecutionBustReasonPriceWrongSpacing, ExecutionBustReasonInvalidFillPrice, ExecutionBustReasonFillExceedsOrderBaseDelta, ExecutionBustReasonFeatureUnavailable, ExecutionBustReasonCollateralIsNotQuote, ExecutionBustReasonCollateralCapExceeded, ExecutionBustReasonCollateralPoolCollision, ExecutionBustReasonOpenInterestExceeded, ExecutionBustReasonAccountType, ExecutionBustReasonNegativeAccountRealBalance, ExecutionBustReasonAccountInsolvent, ExecutionBustReasonSameAccountId, ExecutionBustReasonDecodedLegacy, ExecutionBustReasonUnknown, ExecutionBustReasonUnmapped] = Field(description='''Machine-readable decoded execution-bust reason. This is a discriminated union keyed by `reasonName`. Known contract errors have strict typed shapes; decoded-but-unmodeled ABI errors use the `ExecutionBustReasonUnmapped` fallback with string-valued `args`.''')
  timestamp: int = Field()
  sequence_number: int = Field(alias='''sequenceNumber''')
  fill_id: Optional[str] = Field(description='''Matching-engine fill nonce — a stable identifier to join this bust to its ME fill (PRO-182).''', default=None, alias='''fillId''')
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
    known_object_properties = ['symbol', 'taker_account_id', 'exchange_id', 'maker_account_id', 'taker_order_id', 'maker_order_id', 'qty', 'side', 'price', 'reason', 'timestamp', 'sequence_number', 'fill_id', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0:
      return data

    known_json_properties = ['symbol', 'takerAccountId', 'exchangeId', 'makerAccountId', 'takerOrderId', 'makerOrderId', 'qty', 'side', 'price', 'reason', 'timestamp', 'sequenceNumber', 'fillId', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data
