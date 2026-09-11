from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_exec_api.request_error_code import RequestErrorCode
class RequestError(BaseModel): 
  error: RequestErrorCode = Field(description='''Machine-readable request rejection code. REST returns HTTP 400; the order-entry WebSocket returns the same code in its correlated error response. See the REST API HTTP 400 response reference for per-code meanings and retry guidance.''')
  message: str = Field(description='''Human-readable error message''')
  retry_after_ms: Optional[int] = Field(description='''Minimum wait in milliseconds before retrying the rejected operation. Included for `RATE_LIMITED_ERROR`; omitted for errors without a retry hint, including resting-order caps and `CAPACITY_LIMITED_ERROR`. The same field appears in the REST HTTP 400 body and the order-entry WebSocket's `{ ok: false, error }` response. When present, it is a positive integer. Wait at least this long; it does not reserve capacity, and concurrent requests can consume the account's available budget.''', default=None, alias='''retryAfterMs''')
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
    known_object_properties = ['error', 'message', 'retry_after_ms', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['error', 'message', 'retryAfterMs', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

