# Changelog

All notable changes to the Reya Python SDK are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `sdk.reya_ws_exec.ReyaWsExecClient`: high-level client for the new ws-exec
  WebSocket order-entry service. Mirrors `ReyaTradingClient`'s order surface
  (`create_limit_order`, `create_trigger_order`, `cancel_order`, `mass_cancel`)
  so users never have to hand-craft EIP-712 signatures or wire envelopes.
  Shares `ReyaTradingClient`'s per-wallet nonce manager so REST and WS-exec
  calls from the same process do not collide.
- Spot GTC `expires_after`: client-side validation that rejects past/zero
  values and caps the future-distance at 24h before signing.

### Changed
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
