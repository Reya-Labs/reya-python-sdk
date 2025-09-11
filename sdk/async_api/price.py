from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field

class Price(BaseModel): 
  symbol: str = Field()
  oracle_price: str = Field(description='''Price given by the Stork feeds, used both as the peg price for prices on Reya, as well as Mark Prices. The Stork price feed is usually the perp prices across three major CEXs''', alias='''oraclePrice''')
  pool_price: Optional[str] = Field(description='''The price currently quoted by the AMM for zero volume, from which trades are priced (equivalent to mid price in an order book); a trade of any size will be move this price up or down depending on the direction.''', default=None, alias='''poolPrice''')
  updated_at: int = Field(description='''Last update timestamp (milliseconds)''', alias='''updatedAt''')
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
    known_object_properties = ['symbol', 'oracle_price', 'pool_price', 'updated_at', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['symbol', 'oraclePrice', 'poolPrice', 'updatedAt', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

