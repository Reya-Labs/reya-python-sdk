from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field

class CancelOrderRequest(BaseModel):
  order_id: Optional[str] = Field(description='''Internal matching engine order ID to cancel. Provide `orderId`, or a non-zero `clientOrderId` when `orderId` is absent; if both are supplied the server treats `orderId` as the canonical identifier and `clientOrderId` is ignored. For spot markets, this is the order ID returned in the CreateOrderResponse.''', default=None, alias='''orderId''')
  client_order_id: Optional[str] = Field(description='''Client-provided order ID for tracking and correlation, as a decimal string (`uint64`). Used as the lookup key only when `orderId` is absent, and then it must be non-zero. This is the same clientOrderId provided in CreateOrderRequest.''', default=None, alias='''clientOrderId''')
  account_id: int = Field(alias='''accountId''')
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''')
  signature: str = Field(description='''EIP-712 signature over the `OrderCancel(uint64 verifyingChainId, uint64 deadline, OrderCancelDetails cancel)` envelope. See `docs/eip712.md` for the exact typehash string and signing algorithm.''')
  nonce: str = Field(description='''Monotonically increasing per-signer nonce. A fresh nonce is required per request; replayed nonces are rejected with `INVALID_NONCE_ERROR`. See `docs/eip712.md`.''')
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
    known_object_properties = ['order_id', 'client_order_id', 'account_id', 'symbol', 'signature', 'nonce', 'deadline', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0:
      return data

    known_json_properties = ['orderId', 'clientOrderId', 'accountId', 'symbol', 'signature', 'nonce', 'deadline', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data
