from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_exec_api.order_type import OrderType
from sdk.async_exec_api.time_in_force import TimeInForce
class ModifyOrderRequest(BaseModel): 
  order_id: Optional[str] = Field(description='''Internal matching engine order ID of the order to modify. If present, this is the canonical lookup key; `clientOrderId`, when also present, restates the resting order's immutable client id.''', default=None, alias='''orderId''')
  client_order_id: Optional[str] = Field(description='''Restated client-provided order ID, as a decimal string (`uint64`). Used as the lookup key only when `orderId` is absent, and then it must be non-zero; JSON Schema validates presence only and the server enforces the non-zero rule. If `orderId` is present, this field restates the resting order's immutable client id for signing; omit it when the resting order has no client id. Do not send a placeholder value. The modification cannot assign a new `clientOrderId`.''', default=None, alias='''clientOrderId''')
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''')
  account_id: int = Field(alias='''accountId''')
  exchange_id: int = Field(alias='''exchangeId''')
  is_buy: bool = Field(description='''Order side. Immutable — restate the resting order's value. Combined with `qty`, sets the signed `OrderDetails.quantity` (int256). A mismatch is rejected with `INPUT_VALIDATION_ERROR`.''', alias='''isBuy''')
  order_type: OrderType = Field(description='''Order type aligned with the on-chain `OrderDetails.orderType` enum: LIMIT = limit order, STOP_LOSS = stop-loss trigger order, TAKE_PROFIT = take-profit trigger order.''', alias='''orderType''')
  time_in_force: TimeInForce = Field(description='''Order time in force (IOC = Immediate or Cancel, GTC = Good Till Cancel, GTT = Good Till Time)''', alias='''timeInForce''')
  trigger_px: Optional[str] = Field(default=None, alias='''triggerPx''')
  reduce_only: bool = Field(description='''On-chain `OrderDetails.reduceOnly`. Immutable — restate the resting order's value. A mismatch is rejected with `INPUT_VALIDATION_ERROR`.''', alias='''reduceOnly''')
  limit_px: str = Field(alias='''limitPx''')
  qty: str = Field()
  post_only: bool = Field(description='''The post-modify post-only (maker-only) flag. Always required — send the complete intended value even when it is unchanged from the resting order. If true and the post-modify order would cross, the modification is rejected with `POST_ONLY_WOULD_CROSS_ERROR` and the resting order is unchanged.''', alias='''postOnly''')
  expires_after: Optional[int] = Field(default=None, alias='''expiresAfter''')
  signature: str = Field(description='''Fresh EIP-712 signature over the full post-modify order state — the same `Order` envelope as `createOrder`, with the modified values substituted into `OrderDetails`. See `docs/eip712.md` for the exact typehash string and signing algorithm.''')
  nonce: str = Field(description='''Monotonically increasing per-signer nonce. A fresh nonce is required for every modification; replayed nonces are rejected with `INVALID_NONCE_ERROR`.''')
  signer_wallet: str = Field(alias='''signerWallet''')
  deadline: int = Field()
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
    known_object_properties = ['order_id', 'client_order_id', 'symbol', 'account_id', 'exchange_id', 'is_buy', 'order_type', 'time_in_force', 'trigger_px', 'reduce_only', 'limit_px', 'qty', 'post_only', 'expires_after', 'signature', 'nonce', 'signer_wallet', 'deadline', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['orderId', 'clientOrderId', 'symbol', 'accountId', 'exchangeId', 'isBuy', 'orderType', 'timeInForce', 'triggerPx', 'reduceOnly', 'limitPx', 'qty', 'postOnly', 'expiresAfter', 'signature', 'nonce', 'signerWallet', 'deadline', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

