# Changelog

All notable changes to the Reya Python SDK are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Read-side WebSocket models regenerated from specs 3.1.0: `Order.triggered` —
  the armed-vs-fired discriminator, since both states surface as `OPEN` — and
  the five SL/TP firing `CancelReason` members `OCO_SIBLING_FIRED`,
  `POSITION_CLOSED`, `BAND_VIOLATION`, `RISK_REJECTED` and
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
- Trigger limit-band awareness: `ReyaTradingClient` reads each perp market's
  `triggerLimitBandFraction` at `start()` and refuses an inadmissible trigger
  before a nonce is claimed. The field's three states are distinct — a positive
  fraction enforces `|limit_px - trigger_px| <= trigger_px * fraction` with the
  outermost legal price rounded INWARD to the market's tick, `"0"` disables the
  band and admits any positive `limit_px`, and an ABSENT fraction means the
  market accepts no triggers at all. An absent fraction is NOT read as `"0"`.
  Enforced on both create and modify, mirroring `TRIGGER_LIMIT_OUTSIDE_BAND_ERROR`.
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
- `ReyaTradingClient` re-reads market definitions on a 60s TTL before signing a
  `STOP_LOSS`/`TAKE_PROFIT` create or reprice, matching the API's own cache
  window. `triggerLimitBandFraction` and the tick it rounds to are operationally
  tunable, so a long-lived client no longer enforces the band that was live at
  `start()`. LIMIT orders are bound by neither and never pay for the re-fetch.
- **BREAKING: `TriggerOrderParameters` now REQUIRES `limit_px` and
  `time_in_force`.** `limit_px` is the worst-acceptable execution price of the
  child the trigger fires into; the client no longer synthesizes one when it is
  omitted (the old direction-aware sentinel — one tick for sells, the largest
  tick-aligned price under the ME's MAX_PRICE for buys — is gone, because under
  a per-market trigger limit band there is no price that always executes).
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
  deleted: under a market publishing `triggerLimitBandFraction: "0"` the client
  accepted any positive price at all. The matching engine's MAX_PRICE
  (562949.953421312) is enforced again, independently of the band — `"0"`
  switches off the band, not the ceiling.
- A `STOP_LOSS`/`TAKE_PROFIT` on a spot symbol reported a missing
  `triggerLimitBandFraction` and told the caller to wait for the market to
  publish one. Spot markets arm no triggers at all, so the refusal now says so.
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
