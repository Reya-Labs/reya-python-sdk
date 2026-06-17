from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field

class CancelAllAfterRequest(BaseModel): 
  account_id: int = Field(alias='''accountId''')
  timeout_ms: int = Field(description='''Countdown duration in milliseconds. `0` disarms an armed countdown (no-op if none is armed); any non-zero value must be within [5000, 60000] and arms a fresh countdown of that duration, replacing any previously armed one (re-arming with the same value is the refresh/heartbeat). The exact accepted set is `{0} ∪ [5000, 60000]`; the schema bound is the outer envelope [0, 60000] and the matching engine rejects the [1, 4999] gap with `INPUT_VALIDATION_ERROR`. Signed into the `CancelAllAfter` envelope.''', alias='''timeoutMs''')
  signature: str = Field(description='''EIP-712 signature over the `CancelAllAfter(uint64 verifyingChainId, uint64 deadline, CancelAllAfterDetails cancelAllAfter)` envelope, where `CancelAllAfterDetails(uint64 accountId, uint64 timeoutMs, uint64 nonce)`. See `docs/eip712.md` for the exact typehash string and signing algorithm.''')
  nonce: str = Field(description='''Monotonically increasing per-signer nonce. A fresh nonce is required on every arm/refresh/disarm call; replayed nonces are rejected with `INVALID_NONCE_ERROR`.''')
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
    known_object_properties = ['account_id', 'timeout_ms', 'signature', 'nonce', 'signer_wallet', 'deadline', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['accountId', 'timeoutMs', 'signature', 'nonce', 'signerWallet', 'deadline', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

