from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_exec_api.request_error_code import RequestErrorCode
class RequestError(BaseModel):
  error: RequestErrorCode = Field(description='''Standardized error codes for API responses. The `*_OTHER_ERROR` family (`CREATE_ORDER_OTHER_ERROR`, `CANCEL_ORDER_OTHER_ERROR`, `CANCEL_ALL_AFTER_OTHER_ERROR`, `MODIFY_ORDER_OTHER_ERROR`) is the per-operation catch-all for a matching-engine-side failure that has no more specific code — the human-readable `message` carries the detail. Modify-specific failures also surface as `INPUT_VALIDATION_ERROR` (bad/immutable restate), `ORDER_NOT_FOUND_ERROR`, `EMPTY_MODIFY_ERROR`, `MODIFY_QTY_BELOW_FILLED_ERROR`, or `POST_ONLY_WOULD_CROSS_ERROR`. Branch-able failure reasons lifted out of the `*_OTHER_ERROR` catch-alls so clients can pick a retry/UX strategy without parsing the free-text `message`: `RATE_LIMITED_ERROR` (request throttled — back off and retry; create/modify/cancel), `INSUFFICIENT_BALANCE_ERROR` (collateral too low for the order; create/modify), `OPEN_ORDER_CAP_ERROR` (the account's resting GTC open-order limit is reached; create), `PRICE_QTY_BOUNDS_ERROR` (price or quantity outside the market's accepted bounds; create/modify), `SERVICE_DISABLED_ERROR` (order entry is disabled for this market/instrument — operational kill-switch or market not enabled; create/modify), `UNAUTHORIZED_ACCOUNT_ERROR` (a valid signer that does not own the target order — distinct from `UNAUTHORIZED_SIGNATURE_ERROR`, which is a bad/unauthorized signature; cancel/modify), `TRADING_HALTED_ERROR` (the market is halted; create), and `DUPLICATE_CLIENT_ORDER_ID_ERROR` (the `clientOrderId` is already in use by a live order; create).''')
  message: str = Field(description='''Human-readable error message''')
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
    known_object_properties = ['error', 'message', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0:
      return data

    known_json_properties = ['error', 'message', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data
