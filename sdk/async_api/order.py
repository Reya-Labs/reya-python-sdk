from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_api.side import Side
from sdk.async_api.order_type import OrderType
from sdk.async_api.time_in_force import TimeInForce
from sdk.async_api.order_status import OrderStatus
from sdk.async_api.cancel_reason import CancelReason
class Order(BaseModel): 
  exchange_id: int = Field(alias='''exchangeId''')
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''')
  account_id: int = Field(alias='''accountId''')
  order_id: str = Field(alias='''orderId''')
  sequence_number: Optional[int] = Field(default=None, alias='''sequenceNumber''')
  client_order_id: Optional[str] = Field(description='''Client-provided order ID, as a decimal string (`uint64`). Present when the order has a non-zero client id; omitted otherwise.''', default=None, alias='''clientOrderId''')
  qty: Optional[str] = Field(default=None)
  exec_qty: Optional[str] = Field(default=None, alias='''execQty''')
  cum_qty: Optional[str] = Field(default=None, alias='''cumQty''')
  first_fill_id: Optional[str] = Field(description='''Matching-engine fill nonce of the first fill this update represents. Together with fillCount it identifies the fills as a contiguous nonce range [firstFillId, firstFillId + fillCount - 1]. For a taker update, the first fill of its matching round; for a maker update, its single fill. Present only on fill updates; absent for non-fill updates and resting-order snapshots.''', default=None, alias='''firstFillId''')
  fill_count: Optional[int] = Field(default=None, alias='''fillCount''')
  side: Side = Field(description='''Order side (B = Buy/Bid, A = Ask/Sell)''')
  limit_px: str = Field(alias='''limitPx''')
  order_type: OrderType = Field(description='''Order type aligned with the on-chain `OrderDetails.orderType` enum: LIMIT = limit order, STOP_LOSS = stop-loss trigger order, TAKE_PROFIT = take-profit trigger order.''', alias='''orderType''')
  trigger_px: Optional[str] = Field(default=None, alias='''triggerPx''')
  triggered: Optional[bool] = Field(description='''Armed-vs-fired discriminator for protective stops. `false` while a `STOP_LOSS` / `TAKE_PROFIT` is armed in the matching engine's trigger store: it holds no place in the book, is not executable liquidity, and stays modifiable and cancellable. `true` once it has fired and the resulting child order is resting in the book under the `timeInForce` the create chose. Use it to know that a `modifyOrder` will be refused — a fired child is cancel-only. A fired child's remaining quantity is an upper bound rather than a fillable size: every fill is clamped to the position still reducible at that instant, a clamp to zero cancels the remainder with `cancelReason: POSITION_CLOSED`, and the child is not removed when the position closes by some other route. It blocks the account's own crossing orders through standard self-trade prevention, `POST /v2/cancelAll` cancels it like any other resting order, and an armed cancel-all-after countdown leaves it in place exactly as it leaves an armed trigger. Never `true` on a `LIMIT` order. Omitted by deployments that predate matching-engine trigger firing; treat an omitted value as `false`.''', default=None)
  time_in_force: Optional[TimeInForce] = Field(description='''Order time in force (IOC = Immediate or Cancel, GTC = Good Till Cancel, GTT = Good Till Time)''', default=None, alias='''timeInForce''')
  expires_after: Optional[int] = Field(default=None, alias='''expiresAfter''')
  reduce_only: Optional[bool] = Field(description='''Whether this is a reduce-only order, exclusively used for LIMIT IOC orders. `STOP_LOSS` / `TAKE_PROFIT` orders and the children they fire into are reduce-only by construction — they close the position and can never open one — yet still report `false` here, because this field mirrors the signed on-chain `OrderDetails.reduceOnly`. Do not read `false` on a stop as permission to open a position.''', default=None, alias='''reduceOnly''')
  post_only: Optional[bool] = Field(description='''Whether this is a post-only (maker-only) order. Mirrors `CreateOrderRequest.postOnly`; updated by `modifyOrder`.''', default=None, alias='''postOnly''')
  status: OrderStatus = Field(description='''Order status. An armed but not-yet-fired `STOP_LOSS` / `TAKE_PROFIT` trigger surfaces as `OPEN`, and so does the resting child it fires into; the `triggered` flag on the `Order` schema tells the two apart.''')
  created_at: int = Field(alias='''createdAt''')
  last_update_at: int = Field(alias='''lastUpdateAt''')
  cancel_reason: Optional[CancelReason] = Field(description='''Why an order reached a terminal `CANCELLED` status. `cancelReason` / `cancelReasonMessage` are present if and only if `status` is `CANCELLED`; they are omitted entirely (not `null`, not `""`) otherwise. Matching-engine `UNSPECIFIED` or unknown reasons map to omission. `NO_LIQUIDITY` (IOC executed zero quantity because no fillable liquidity was available at its limit) and `IOC_REMAINDER` (IOC partially filled and the remaining quantity was cancelled because no more executable opposite liquidity was available) are returned on `createOrder` / `modifyOrder` responses; `GTT_EXPIRED`, `USER_CANCEL`, `MASS_CANCEL` and `CANCEL_ALL_AFTER` are delivered on the `walletOrderChanges` stream. `SELF_TRADE_PREVENTION` can appear on `createOrder` / `modifyOrder` responses and on `walletOrderChanges` when a modified resting order crosses as taker. It cancels the aggressor (taker) only; your resting order is left untouched. If an IOC partially fills and its remaining quantity is then cancelled by self-trade prevention, the response keeps the fill quantities and reports `cancelReason: SELF_TRADE_PREVENTION`; `IOC_REMAINDER` is used only when matching stops without a self-cross. `FEED_RESET` is generated by the API layer (not the matching engine): when the market-data feed is reset — e.g. a matching-engine reseed — every resting order is delivered on the `walletOrderChanges` stream as `CANCELLED` with `cancelReason: FEED_RESET`, immediately followed by fresh order events that re-publish your current open orders. It does not mean your orders were cancelled on the exchange; treat it as a signal to discard and rebuild your local order view, reconciling against `GET /v2/wallet/{address}/openOrders` if needed. `RISK_CANCELLED` means one of your resting orders was cancelled by the exchange's pre-trade risk checks at the moment it was about to be matched — typically because your account can no longer support the position that fill would create; `cancelReasonMessage` carries which check fired. It is delivered on the `walletOrderChanges` stream only, and never on a `createOrder` / `modifyOrder` response: an order refused by risk checks at admission was never created and comes back as an error response carrying a `RequestErrorCode` instead, so a cancellation always refers to an order that existed and has an `orderId`. If the cancelled order had already partially filled, the cancellation keeps its fill quantities. The incoming order that triggered the match is not itself cancelled and the match cycle is not aborted: it continues against the remaining book, which may leave it filling at worse levels, or not at all. The remaining reasons belong to protective stops and are delivered on the `walletOrderChanges` stream. `OCO_SIBLING_FIRED`: a stop-loss and a take-profit on the same account and market are one-cancels-the-other, so whichever fires cancels the other leg — including a fire that ends up filling nothing, which still consumes both legs. Re-arming the remaining protection is always the user's move; cancelling one leg by hand leaves the sibling armed. `PROTECTIVE_SELF_TRADE_SWEEP`: a firing stop that would match the account's own resting liquidity sweeps those resting maker orders out of the way so the protective child can execute. It is delivered on the swept MAKER order's own cancellation event — the resting order carries this reason as it goes terminal — which is what distinguishes it from `SELF_TRADE_PREVENTION`: that one appears on the incoming taker and leaves resting orders untouched. `POSITION_CLOSED`: a fired child had nothing left to reduce. Every fill of a fired child is clamped to the position still reducible at that moment, and a clamp to zero cancels the remainder. `RISK_REJECTED` is the admission-time counterpart of `RISK_CANCELLED`: the child a trigger fired into was refused by the same pre-trade risk checks that return a `RequestErrorCode` on an interactive create. Because no request is waiting on it, the refusal is published on the order instead, with `cancelReasonMessage` carrying which check fired. The consequence is terminal: the trigger is cancelled, nothing rests and nothing fills, and the protection is gone — the stop is not retried or re-armed once the account recovers, so re-arming is the user's move. `BAND_VIOLATION`: armed triggers whose `limitPx` no longer satisfies the market's `triggerLimitBandFraction` — the band configuration changed underneath them — are cancelled at matching-engine startup rather than admitted at a limit price the venue would now refuse; re-place the affected stops with a `limitPx` inside the current band. A fired `GTT` child that reaches the trigger's signed `expiresAfter` carries the ordinary `GTT_EXPIRED`, and `POST /v2/cancelAll` cancels a fired child as `MASS_CANCEL` like any other resting order.''', default=None, alias='''cancelReason''')
  cancel_reason_message: Optional[str] = Field(description='''Human-readable explanation of `cancelReason`. Present only when `cancelReason` is present.''', default=None, alias='''cancelReasonMessage''')
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
    known_object_properties = ['exchange_id', 'symbol', 'account_id', 'order_id', 'sequence_number', 'client_order_id', 'qty', 'exec_qty', 'cum_qty', 'first_fill_id', 'fill_count', 'side', 'limit_px', 'order_type', 'trigger_px', 'triggered', 'time_in_force', 'expires_after', 'reduce_only', 'post_only', 'status', 'created_at', 'last_update_at', 'cancel_reason', 'cancel_reason_message', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['exchangeId', 'symbol', 'accountId', 'orderId', 'sequenceNumber', 'clientOrderId', 'qty', 'execQty', 'cumQty', 'firstFillId', 'fillCount', 'side', 'limitPx', 'orderType', 'triggerPx', 'triggered', 'timeInForce', 'expiresAfter', 'reduceOnly', 'postOnly', 'status', 'createdAt', 'lastUpdateAt', 'cancelReason', 'cancelReasonMessage', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

