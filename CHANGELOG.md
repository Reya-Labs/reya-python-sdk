# Changelog

All notable changes to the Reya Python SDK are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Read-side WebSocket models regenerated from specs 3.1.0: `Order.triggered` —
  the armed-vs-fired discriminator, since both states surface as `OPEN` — and
  the four SL/TP firing `CancelReason` members `OCO_SIBLING_FIRED`,
  `POSITION_CLOSED`, `RISK_REJECTED` and
  `PROTECTIVE_SELF_TRADE_SWEEP`. Until now `ReyaSocket` raised
  `WebSocketDataError` out of `on_message` on the first frame carrying any of
  them — which is every fired stop, because `OCO_SIBLING_FIRED` publishes on
  every fire.
- Typed read-side WebSocket account discovery via
  `socket.wallet.accounts(address)`, generated from the canonical
  `/v2/wallet/{address}/accounts` AsyncAPI channel.
- Trigger-order repricing: `ModifyOrderParameters` gains an `order_type` field
  (default `LIMIT`), and `build_modify_order_payload` signs and wires that order
  type instead of a hardcoded `LIMIT`. A `STOP_LOSS`/`TAKE_PROFIT` modify now
  reprices an armed trigger and requires `trigger_px` (rejected before signing
  otherwise). Its `qty` is now typed `Optional[str]`: a trigger modify passes
  `qty=None` (the signed quantity restates the ±int256.max full-position
  sentinel — protect the whole position — and `qty` is dropped from the wire), while a LIMIT modify still requires a real
  `qty`. Not a breaking change: `qty` keeps its original positional slot with no
  default and the new `order_type` is appended last, so every positional
  `ModifyOrderParameters(...)` call that was valid in 3.0.14 still binds
  unchanged; the default keeps LIMIT modifies byte-identical, and the trigger
  create/cancel wire contract is unchanged (omit `qty`; the signed quantity is
  the ±int256.max full-position sentinel, sign from `is_buy`). The live trigger
  create/modify/cancel e2e tests are staged (skipped) until the SL/TP backbone
  matching engine is deployed to devnet1.
- Server-extended enums degrade instead of breaking: `CancelReason`,
  `OrderStatus`, `RequestErrorCode`, `WsExecErrorCode`, `ExecutionType`,
  `AccountType` and `TierType` gain an `UNKNOWN` member that a value this SDK
  has never heard of resolves to. A matching engine that allocates a new
  cancel reason no longer costs the read-side stream a silently dropped frame,
  or a ws-exec caller an unparseable response to an order the server already
  acted on. The order-entry vocabularies (`OrderType`, `TimeInForce`) are
  deliberately excluded — a request the client cannot encode still fails loudly.
- `sdk.reya_ws_exec.ReyaWsExecClient`: high-level client for the new ws-exec
  WebSocket order-entry service. Mirrors `ReyaTradingClient`'s order surface
  (`create_limit_order`, `create_trigger_order`, `cancel_order`, `mass_cancel`)
  so users never have to hand-craft EIP-712 signatures or wire envelopes.
  Shares `ReyaTradingClient`'s per-wallet nonce manager so REST and WS-exec
  calls from the same process do not collide.
- Spot GTC `expires_after`: client-side validation that rejects past/zero
  values and caps the future-distance at 24h before signing.

### Changed
- **BREAKING (generated read-side models): `sdk.async_api.depth.Depth` is
  replaced by `sdk.async_api.depth_snapshot.DepthSnapshot` and
  `sdk.async_api.depth_update.DepthUpdate`.** The subscribe frame carries a
  bounded `DepthSnapshot` (at most 100 levels per side on the WebSocket) and
  every `channel_data` frame carries a `DepthUpdate` describing the transition
  from one bounded view to the next; `MarketDepthUpdatePayload.data` is now
  typed `DepthUpdate`. Field names and their semantics are unchanged, so
  migration is the import and the type name. The REST `sdk.open_api` `Depth`
  model is unaffected.
- A LIMIT modify now refuses `reduce_only=True` and a restated `IOC`
  `time_in_force` client-side, matching the guards the trigger-modify path
  already had. Neither shape can name a resting order — reduce-only is
  perp-IOC-only and IOC never rests — so both were guaranteed server rejections
  bought with a spent nonce.
- **BREAKING: `TriggerOrderParameters` now REQUIRES `limit_px` and
  `time_in_force`.** `limit_px` is the worst-acceptable execution price of the
  child the trigger fires into; the client no longer synthesizes one when it is
  omitted (the old direction-aware sentinel — one tick for sells, the largest
  tick-aligned price under the ME's MAX_PRICE for buys — is gone: the venue
  admits a fired child's limit price on its own rules, so there is no price
  that always executes).
  `time_in_force` chooses what the stop BECOMES when it fires (`GTC`/`GTT`/`IOC`)
  and flows into both the EIP-712 digest and the `timeInForce` wire key, which
  the backend now requires on every create. A `GTT` trigger must carry a future
  `expires_after` — one deadline covering both the armed trigger's lifetime and
  the fired child's on-chain settlement, so the settlement-headroom rule applies
  to it — while `GTC` and `IOC` triggers must omit it. Migration: pass the two
  new fields explicitly; a call that omitted them raises `TypeError` rather than
  signing a price you did not choose. Note that `time_in_force` is inserted
  BEFORE `expires_after`, and this is a plain `@dataclass` (no runtime type
  validation): a caller that passed `expires_after` POSITIONALLY binds it to
  `time_in_force` silently instead of raising. Pass trigger parameters by
  keyword.
- Trigger modifies no longer require `GTC`. `time_in_force` and `expires_after`
  are restate-immutable on an armed trigger: restating the armed values is
  admitted, and a change that lands on an impossible shape is refused
  client-side with a message pointing at cancel-and-recreate.
- The ws-exec quickstart now defaults to the current devnet endpoint, exposes
  offline-testable URL/order builders, and links to the actual pytest live
  suite instead of the removed `tests/ws_exec/mvp.py` harness.
- **BREAKING (server-driven, SDK-passthrough): `start_time` / `end_time` on
  market-data executions, busts, and candle endpoints are now interpreted by
  the server as Unix-milliseconds since epoch (not sequence numbers).** The
  Python parameter type is unchanged (`Optional[int]`) so this is invisible
  to typed callers, but pagination code that was using `last.sequence_number`
  needs to switch to `last.timestamp`. Affected endpoints:
  - `MarketDataApi.get_market_perp_executions`
  - `MarketDataApi.get_market_spot_executions`
  - `MarketDataApi.get_market_spot_execution_busts`
  - `WalletDataApi.get_wallet_perp_executions`
  - `WalletDataApi.get_wallet_spot_executions`
  - `WalletDataApi.get_wallet_spot_execution_busts`
  - `MarketDataApi.get_candle_history`

  Migration:
  ```python
  # Before
  page2 = await client.markets.get_market_perp_executions(
      symbol, start_time=last.sequence_number
  )
  # After
  page2 = await client.markets.get_market_perp_executions(
      symbol, start_time=last.timestamp,  # ms since epoch
  )
  ```

  A follow-up SDK release will introduce explicit `start_time_ms` /
  `end_time_ms` parameter names so the unit is in the symbol — tracked
  in the spec repo.

### Fixed
- Trigger admission lost its price ceiling when the `limit_px` sentinel was
  deleted: the client accepted any positive price at all. Both `limit_px` and
  `trigger_px` are checked against the matching engine's price domain again —
  MIN_PRICE (0.000000001) through MAX_PRICE (562949.953421312), the same domain
  the engine's own `validate_place` enforces. Below MIN_PRICE the E18 scaling in
  the signer truncated the price to zero, so a sub-nano price used to be signed
  as 0 before the wire string was refused. The per-market trigger limit band is
  NOT mirrored client-side: it is a matching-engine setting, it is published
  nowhere, and a `limit_px` outside it comes back as
  `TRIGGER_LIMIT_OUTSIDE_BAND_ERROR`.
- A `STOP_LOSS`/`TAKE_PROFIT` on a spot symbol was refused with a message about
  market metadata. Spot markets arm no triggers at all, so the refusal now says
  so.
- A plain string where the SDK documents an enum (`time_in_force="GTC"`,
  `trigger_type="STOP_LOSS"`) claimed the per-wallet nonce and then died on
  `AttributeError: 'str' object has no attribute 'value'` while building the
  wire payload — the order was never sent, but the counter had advanced.
  `TimeInForce`/`OrderType` are now normalised in one place ahead of the nonce,
  so the string form is accepted and produces the same payload as the enum,
  and an unrecognised value raises the named `ValueError` with the nonce
  untouched.
- `is_buy` is now required to be a real `bool` on all three builders. A truthy
  string such as `"false"` signed the buy-side sentinel while the ws-exec wire
  coerced `isBuy: false`, which the venue could only report as a signature
  mismatch.
- Every price the builders emit is rendered with `format(value, "f")`. Only the
  trigger-create `limitPx` was; `triggerPx`, the LIMIT-create `limitPx` and both
  modify prices used `str()`, so a `Decimal("0.0000001")` reached the wire as
  `"1E-7"` and the server's ethers `FixedNumber` parser rejected it with
  `INVALID_ARGUMENT` — after the nonce and the signature.
- Repricing a GTT order in its final minute is no longer refused for a deadline
  the client chose itself. The DEFAULT `deadline` is clamped to just under an
  `expires_after` nearer than 60s, so restating an armed trigger's
  restate-immutable expiry — the only legal thing a reprice does with it — is
  admitted. An explicitly passed `deadline` is unchanged.
- `limit_px=None` raised a bare `TypeError` from `Decimal`, and a `None` or
  unmapped `time_in_force` carrying an `expires_after` raised `AttributeError`
  from the coupling message's own f-string, shadowing the intended
  "Unsupported time_in_force" `ValueError`. Both now name the field.
- The per-wallet nonce was claimed before the last few refusals on the create
  and modify paths, so a rejected order still advanced the counter. An
  unmapped `time_in_force` / `order_type` now raises a named `ValueError`
  instead of a bare `KeyError`, and every refusal on all three builders
  precedes the nonce.
- `build_create_limit_order_payload` and `build_modify_order_payload` refuse an
  `IOC` order carrying `expires_after`. IOC never rests, so the server rejected
  it after the nonce was already spent; the trigger path already covered all
  three time-in-force arms.
- `examples/rest_api/spot/test_rate_limit.py` was matching pytest's default
  test-collection pattern and would have been picked up by `pytest`, running
  8 verification tests against a live API per run. Renamed to
  `verify_rate_limits.py`; `examples/` is now excluded from `pyproject.toml`'s
  `norecursedirs`; added a hard mainnet guard.
- `verify_rate_limits.py:269` no longer raises `IndexError` when Step 1
  rejected both orders — it now logs a warning and returns early.
- `depth_market_maker.py`: oracle-dead path no longer quotes ETH at the
  hardcoded `$0.10` fallback. The market maker now halts the cycle when
  `reference_price == 0`. The `with_http_retry` wrapper now (a) takes an
  explicit `is_idempotent` flag and refuses to retry create-order calls
  unless the caller passes a stable `client_order_id` and (b) recognises
  HTTP-400 `"rate limit"` bodies as transient. The startup `mass_cancel`
  is now wrapped in `with_http_retry`, and `state.open_orders.clear()`
  is now lock-protected.
