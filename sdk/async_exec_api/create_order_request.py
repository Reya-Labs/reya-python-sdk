from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_exec_api.order_type import OrderType
from sdk.async_exec_api.time_in_force import TimeInForce
class CreateOrderRequest(BaseModel): 
  exchange_id: int = Field(alias='''exchangeId''')
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''')
  account_id: int = Field(alias='''accountId''')
  is_buy: bool = Field(description='''Whether this is a buy order. Combined with `qty`, determines the signed `OrderDetails.quantity` (int256): positive for buy/long, negative for sell/short.''', alias='''isBuy''')
  limit_px: str = Field(alias='''limitPx''')
  qty: Optional[str] = Field(default=None)
  order_type: OrderType = Field(description='''Order type aligned with the on-chain `OrderDetails.orderType` enum: LIMIT = limit order, STOP_LOSS = stop-loss trigger order, TAKE_PROFIT = take-profit trigger order.''', alias='''orderType''')
  time_in_force: Optional[TimeInForce] = Field(description='''Order time in force (IOC = Immediate or Cancel, GTC = Good Till Cancel, GTT = Good Till Time)''', default=None, alias='''timeInForce''')
  trigger_px: Optional[str] = Field(default=None, alias='''triggerPx''')
  reduce_only: Optional[bool] = Field(description='''Reduce-only intent. Perp only; spot markets must set this to false. Maps to on-chain `OrderDetails.reduceOnly`.''', default=None, alias='''reduceOnly''')
  post_only: Optional[bool] = Field(description='''Post-only (maker-only) intent: the order must rest and never cross as a taker. Valid on GTC/GTT; rejected on IOC. Maps to on-chain `OrderDetails.postOnly`.''', default=None, alias='''postOnly''')
  signature: str = Field(description='''EIP-712 signature over the `Order(uint256 verifyingChainId, uint256 deadline, OrderDetails order)` envelope. See `docs/eip712.md` for the exact typehash string and signing algorithm.''')
  nonce: str = Field(description='''Monotonically increasing per-signer nonce. Maps to on-chain `OrderDetails.nonce`.''')
  signer_wallet: str = Field(alias='''signerWallet''')
  deadline: int = Field()
  expires_after: Optional[int] = Field(default=None, alias='''expiresAfter''')
  client_order_id: Optional[int] = Field(default=None, alias='''clientOrderId''')
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
    known_object_properties = ['exchange_id', 'symbol', 'account_id', 'is_buy', 'limit_px', 'qty', 'order_type', 'time_in_force', 'trigger_px', 'reduce_only', 'post_only', 'signature', 'nonce', 'signer_wallet', 'deadline', 'expires_after', 'client_order_id', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['exchangeId', 'symbol', 'accountId', 'isBuy', 'limitPx', 'qty', 'orderType', 'timeInForce', 'triggerPx', 'reduceOnly', 'postOnly', 'signature', 'nonce', 'signerWallet', 'deadline', 'expiresAfter', 'clientOrderId', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

