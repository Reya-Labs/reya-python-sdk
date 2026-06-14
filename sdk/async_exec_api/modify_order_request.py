from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field

class ModifyOrderRequest(BaseModel): 
  order_id: Optional[str] = Field(description='''Internal matching engine order ID of the order to modify. Exactly one of `orderId` or `clientOrderId` must be provided.''', default=None, alias='''orderId''')
  client_order_id: Optional[int] = Field(default=None, alias='''clientOrderId''')
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''')
  account_id: int = Field(alias='''accountId''')
  limit_px: str = Field(alias='''limitPx''')
  qty: str = Field()
  post_only: bool = Field(description='''The post-modify post-only (maker-only) flag. Always required — send the complete intended value even when it is unchanged from the resting order. If true and the post-modify order would cross, the modification is rejected with `POST_ONLY_WOULD_CROSS_ERROR` and the resting order is unchanged.''', alias='''postOnly''')
  expires_after: int = Field(alias='''expiresAfter''')
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
    known_object_properties = ['order_id', 'client_order_id', 'symbol', 'account_id', 'limit_px', 'qty', 'post_only', 'expires_after', 'signature', 'nonce', 'signer_wallet', 'deadline', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['orderId', 'clientOrderId', 'symbol', 'accountId', 'limitPx', 'qty', 'postOnly', 'expiresAfter', 'signature', 'nonce', 'signerWallet', 'deadline', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

