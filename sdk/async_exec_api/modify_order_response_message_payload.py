from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from sdk.async_exec_api.modify_order_message_type import ModifyOrderMessageType
from sdk.async_exec_api.modify_order_response import ModifyOrderResponse
from sdk.async_exec_api.request_error import RequestError
class ModifyOrderResponseMessagePayload(BaseModel): 
  type: ModifyOrderMessageType = Field(description='''Message type for modifyOrder request and response''')
  id: str = Field(description='''Echoes the request `id`.''')
  ok: bool = Field(description='''True on success (with `payload`), false on failure (with `error`).''')
  payload: Optional[ModifyOrderResponse] = Field(description='''Result of a modification, same shape as `CreateOrderResponse` plus `execQty` / `cumQty`. `orderId` is always the same ID the order had before the modification. If the modification crossed the book it executed immediately: `execQty` carries the quantity it filled and `status` reflects the outcome (`OPEN` for a partial fill leaving a remainder resting, `FILLED` for a complete fill, or `CANCELLED` when a non-post-only crossing modify self-matches the account's own resting liquidity — self-match prevention cancels the taker, so the order is removed and rests nowhere). Per-fill detail (prices, fees) is delivered on the wallet executions and `walletOrderChanges` streams, exactly as for `createOrder`.''', default=None)
  error: Optional[RequestError] = Field(default=None)
