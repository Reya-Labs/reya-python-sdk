from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.create_order_message_type import CreateOrderMessageType
from sdk.async_exec_api.create_order_request import CreateOrderRequest
class CreateOrderRequestMessagePayload(BaseModel): 
  type: CreateOrderMessageType = Field(description='''Message type for createOrder request and response''')
  id: str = Field(description='''Client-chosen correlation identifier; must be unique across in-flight requests on the connection.''')
  payload: CreateOrderRequest = Field(description='''Order creation request. The fields carried here mirror the on-chain `OrderDetails` struct that the client signs via EIP-712. The REST surface keeps `isBuy` + `qty` as separate fields for symmetry with the rest of the API (Order, Execution, Trade schemas); on the signing side the signed `OrderDetails.quantity` is reconstructed as the signed int256 `isBuy ? +qty : -qty`. Two distinct time-related fields are carried and signed: `deadline` (signature validity — enforced by the API at entry) and `expiresAfter` (order lifetime — enforced on-chain at execution). Convention: if `expiresAfter > 0`, clients should ensure `deadline <= expiresAfter` to avoid signing a dead-on-arrival order. See `docs/eip712.md` for the signing algorithm and exact typehash strings.''')
