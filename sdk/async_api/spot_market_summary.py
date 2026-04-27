from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field

class SpotMarketSummary(BaseModel): 
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''')
  updated_at: int = Field(alias='''updatedAt''')
  volume24h: str = Field()
  px_change24h: Optional[str] = Field(default=None, alias='''pxChange24h''')
  oracle_price: Optional[str] = Field(default=None, alias='''oraclePrice''')
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
    known_object_properties = ['symbol', 'updated_at', 'volume24h', 'px_change24h', 'oracle_price', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['symbol', 'updatedAt', 'volume24h', 'pxChange24h', 'oraclePrice', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

