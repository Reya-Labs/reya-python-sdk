from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_api.transfer_type import TransferType
class Transfer(BaseModel): 
  sequence_number: int = Field(alias='''sequenceNumber''')
  account_id: int = Field(alias='''accountId''')
  asset: str = Field()
  amount: str = Field()
  net_deposits_after: str = Field(alias='''netDepositsAfter''')
  type: TransferType = Field(description='''Purpose of a transfer entry; direction is given by the sign of amount. See GET /v2/wallet/{address}/transfers, Transfer types, for the meaning of each value.''')
  counterparty_account_id: Optional[int] = Field(default=None, alias='''counterpartyAccountId''')
  symbol: Optional[str] = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''', default=None)
  spot_execution_sequence_number: Optional[int] = Field(default=None, alias='''spotExecutionSequenceNumber''')
  fill_id: Optional[str] = Field(description='''Fill identifier linking this entry to PerpExecution.fillId or SpotExecution.fillId. Omitted for deposits, liquidations, auto-exchanges, and dust settlements; historical perp fee entries may also omit it.''', default=None, alias='''fillId''')
  timestamp: int = Field()
  transaction_hash: str = Field(description='''Hash of the transaction that emitted the transfer''', alias='''transactionHash''')
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
    known_object_properties = ['sequence_number', 'account_id', 'asset', 'amount', 'net_deposits_after', 'type', 'counterparty_account_id', 'symbol', 'spot_execution_sequence_number', 'fill_id', 'timestamp', 'transaction_hash', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['sequenceNumber', 'accountId', 'asset', 'amount', 'netDepositsAfter', 'type', 'counterpartyAccountId', 'symbol', 'spotExecutionSequenceNumber', 'fillId', 'timestamp', 'transactionHash', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

