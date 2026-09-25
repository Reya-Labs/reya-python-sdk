from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field

class MassCancelRequest(BaseModel): 
  account_id: int = Field(alias='''accountId''')
  symbol: Optional[str] = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''', default=None)
  signature: str = Field(description='''EIP-712 signature over the `MassCancel(uint64 verifyingChainId, uint64 deadline, MassCancelDetails massCancel)` envelope. See the EIP-712 signing reference in the Reya docs (https://docs.reya.xyz/developers/readme/signatures-and-nonces) for the exact typehash string and signing algorithm.''')
  nonce: str = Field(description='''Monotonically increasing per-signer nonce. A fresh nonce is required per request; replayed nonces are rejected with `INVALID_NONCE_ERROR`. See the EIP-712 signing reference in the Reya docs (https://docs.reya.xyz/developers/readme/signatures-and-nonces).''')
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
    known_object_properties = ['account_id', 'symbol', 'signature', 'nonce', 'deadline', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['accountId', 'symbol', 'signature', 'nonce', 'deadline', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

