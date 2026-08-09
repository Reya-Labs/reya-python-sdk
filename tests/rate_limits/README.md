# Rate-Limit v1 test suite

Coverage for Rate-Limit v1 (whitelist gate, per-account GCRA buckets, open-order
caps, reactive eject) across REST and ws-exec.

The suite is **configuration-driven, not load-generating**. It never tries to
out-run a production-sized limit; it expects the target deployment (localnet) to
expose **small Standard-tier limits** so a handful of requests trips them, and to
seed the standard test wallets into `rl_whitelist` (heavy wallets into
`rl_market_makers`).

## Layout

| Module | Runs | What it covers |
| --- | --- | --- |
| `test_offline_error_surface.py` | **always** (`-m offline`) | The SDK surfaces 429/503/403 with status + headers + parsed error code + `retryAfterMs`; extraction works before and after SDK regeneration |
| `test_whitelist_gate.py` | opt-in | §3 gate: create + modify rejected `403 NOT_WHITELISTED_ERROR`; cancel / cancelAll / cancelAllAfter asserted **not** gated (risk-off carve-out); reads unaffected |
| `test_gcra_buckets.py` | opt-in | §4.1 place bucket → `429 RATE_LIMITED_ERROR` + `Retry-After`; the **cancel carve-out** (risk-off flows while place-limited); recovery after `Retry-After`; cancel/place bucket independence |
| `test_open_order_caps.py` | opt-in | §4.2 count cap → `OPEN_ORDER_COUNT_EXCEEDED_ERROR`; notional cap on a qty-up modify → `OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR`; IOC exempt |
| `test_eject_flow.py` | opt-in + eject hook | §3 reactive eject: creates **and modifies** blocked 403-class, **cancels still flow**, **resting** orders swept while **armed SL/TP are retained**, un-eject restores trading |
| `test_ws_exec_parity.py` | opt-in + `REYA_WS_EXEC_URL` | §8 ws-exec envelope carries the same codes (+ `retryAfterMs`) |

Run them with:

```bash
# offline only — no deployment needed
poetry run pytest tests/rate_limits -m offline

# the live suite (needs RL_TEST_ENABLED=1 and a rate-limited deployment)
poetry run pytest tests/rate_limits -m rate_limits -ra
```

Without `RL_TEST_ENABLED` the live modules still **collect** (proving they
import) and skip with an explanatory reason.

## Gate scope (design ruling, 2026-07-31)

The whitelist gate covers **create and modify only**. Cancel, cancelAll and
cancelAllAfter are **not** gated, and the ME's ejected-account check rejects
creates/modifies rather than cancels. Risk-off always flows: a de-whitelisted or
ejected owner must still be able to unwind its book and keep its dead-man's
switch alive, and its resting orders are removed by the ME sweep rather than by
denying it the ability to cancel.

The same split holds for ejected accounts: the ME refuses an ejected account's
**create and modify** (both risk-increasing) with `ACCOUNT_SUSPENDED_ERROR`,
while its cancels, mass-cancel and COD ops stay open and its resting orders are
removed by the ME sweep.

The suite encodes this asymmetry directly — `assert_not_whitelist_gated`
(`rl_actions.py`) accepts any outcome for a risk-off op *except* a gate verdict
(`NOT_WHITELISTED_ERROR`, or any 403-class status), so it stays valid whether
the deployment answers with a success or an order-not-found-class error.

## Eject sweep scope (design ruling, 2026-08)

The eject sweep clears **resting** orders and deliberately **retains armed
stop-loss / take-profit orders**. Ejecting an account does not close its
positions, so cancelling its protective stops would leave it unprotected in
exactly the situation the eject was meant to de-risk. The invariant is "an
ejected account holds no resting liquidity", **not** "an ejected account has an
empty book".

**Retention is a property of the SWEEP, not of mass-cancel.** The ME's cancel
path takes a `skip_protective_stops` flag and the three callers differ:

| Caller | Protective stops |
| --- | --- |
| Eject sweep | **retained** (this ruling) |
| COD fire (dead-man's switch) | **retained** |
| User-initiated `cancelAll` / mass-cancel | **cancelled** |

So an explicit user cancelAll does empty the book, triggers included — that is
the user asking for it, not the system de-risking them. Do not generalise the
eject ruling into "triggers are never mass-cancelled". A practical consequence
for this suite: `ensure_flat` cancels through mass-cancel, so it *will* clear a
leftover probe trigger; there is no leak risk into later tests.

`test_eject_flow.py` polls `resting_order_ids` (non-trigger orders only) for the
sweep, and — when the harness can arm one — asserts that an armed stop is still
open afterwards. Arming is best-effort: SL/TP is perp-only while the rest of the
suite is spot-only, so the retention assertion runs only when
`RL_TEST_TRIGGER_SYMBOL` points at a perp market, and is logged as not-probed
otherwise. Every refusal path (unknown symbol, no mark price, a stop already
armed) skips the probe rather than failing the eject test.

**Arming does NOT require an open position.** The trigger contract is
whole-position-at-fire-time: the client omits `qty`, the EIP-712 envelope signs
the ±int256.max full-position sentinel, and the close size is derived from the
live position *when the trigger fires*. The arm-time gates are only "perp
market" and "at most one STOP_LOSS and one TAKE_PROFIT per (account, market)" —
the latter being what raises `TRIGGER_ALREADY_EXISTS_ERROR`. The localnet wiring
can therefore point `RL_TEST_TRIGGER_SYMBOL` at any perp market, with no need to
pre-open a position on it.

Note the second-order effect on the whitelist gate: the same ruling is why a
de-whitelisted owner keeps its stops. The gate blocks create and modify, so it
cannot arm a NEW stop while gated — the ones already armed simply stay.

The remaining risk-off carve-out is unchanged — `assert_not_whitelist_gated`
(`rl_actions.py`) accepts any outcome for a risk-off op *except* a gate verdict
(`NOT_WHITELISTED_ERROR`, or any 403-class status), so it stays valid whether
the deployment answers with a success or an order-not-found-class error.

### Deferred case: a real COD arm for a de-whitelisted wallet

`test_cancel_all_after_is_not_gated` probes cancelAllAfter with `timeoutMs=0`
(disarm), which is a server-side no-op and therefore safe to run against a
shared account. Proving that a **real arm** (a non-zero timeout) also survives
de-whitelisting needs a throwaway account whose book can be sacrificed when the
countdown fires — deferred to the localnet wiring.

## Environment knobs

### Gate

| Variable | Default | Meaning |
| --- | --- | --- |
| `RL_TEST_ENABLED` | *(off)* | `1`/`true` enables every live module. Everything else in this table is only read when it is set. |

### Accounts

| Variable | Default | Meaning |
| --- | --- | --- |
| `RL_TEST_ACCOUNT_ID` | `SPOT_ACCOUNT_ID_1` | Whitelisted, **Standard-tier** account the bucket/cap/eject tests drive |
| `RL_TEST_PRIVATE_KEY` | `SPOT_PRIVATE_KEY_1` | its signing key |
| `RL_TEST_WALLET_ADDRESS` | `SPOT_WALLET_ADDRESS_1` | its **owner** wallet (the whitelist/eject key) |
| `RL_TEST_NON_WHITELISTED_ACCOUNT_ID` | *(none)* | An account the deployment deliberately did **not** seed into `rl_whitelist`. No fallback — guessing would turn the gate test into a false pass |
| `RL_TEST_NON_WHITELISTED_PRIVATE_KEY` | *(none)* | its signing key |
| `RL_TEST_NON_WHITELISTED_WALLET_ADDRESS` | *(none)* | its owner wallet |

The account under test must **not** be in `rl_market_makers`, or the MM tier's
much larger limits will make the burst tests time out on their attempt bound.

### Market

| Variable | Default | Meaning |
| --- | --- | --- |
| `RL_TEST_SYMBOL` | `WETHRUSD` | Spot market used for all resting orders. Skips if absent from `/v2/spotMarketDefinitions` |
| `RL_TEST_TRIGGER_SYMBOL` | *(none)* | Optional **perp** market on which `test_eject_flow.py` arms one far-out-of-the-money stop, to prove the sweep **retains** it. No default and no fallback: unset means the retention assertion is logged as not-probed and the rest of the eject flow still runs |

Spot is used deliberately: a far-below-oracle GTC BUY rests forever, so the
suite needs no counterparty, no fills, and no settlement. SL/TP is perp-only, so
the trigger-retention probe is the one place the suite reaches for a perp
market — and it is optional for exactly that reason.

### Standard-tier limits as deployed

These describe what the deployment is configured with. They size attempt bounds
and pacing only — **no test asserts GCRA arithmetic**.

| Variable | Default | Meaning |
| --- | --- | --- |
| `RL_TEST_STANDARD_PLACE_PER_MIN` | `60` | sustained `place` rate (create + modify) |
| `RL_TEST_STANDARD_PLACE_BURST` | `5` | `place` burst tolerance (τ) |
| `RL_TEST_STANDARD_CANCEL_PER_MIN` | `120` | sustained single-`cancel` rate |
| `RL_TEST_STANDARD_CANCEL_BURST` | `10` | `cancel` burst tolerance |
| `RL_TEST_STANDARD_BULK_CANCEL_PER_MIN` | `10` | `bulk-cancel` rate (mass-cancel / CoD fire) — informational; cleanup backs off when it bites |
| `RL_TEST_STANDARD_OPEN_ORDER_COUNT_CAP` | `10` | resting-order count cap; the localnet wiring exports the value matching the ME's localnet env, so the default is only a fallback |
| `RL_TEST_STANDARD_OPEN_NOTIONAL_CAP` | `5000` | open-notional cap, in quote units (RUSD) |

The notional test skips unless the account holds at least **1.5x the notional
cap** in free RUSD, so the reject under test is the cap and not
`INSUFFICIENT_BALANCE_ERROR`. Funding the account to that level (or tuning the
knob down) is on the localnet wiring checklist.

**Cap error codes.** The ME caps emit only the new codes
(`OPEN_ORDER_COUNT_EXCEEDED_ERROR` / `OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR`); the
legacy `OPEN_ORDER_CAP_ERROR` is **replaced, not aliased**. During the
transition the legacy API-side cap still exists and still emits the legacy code
until its P17 removal, so a run against an environment where the legacy cap is
tighter than the ME's could surface `OPEN_ORDER_CAP_ERROR` first. These tests
target the ME caps (Standard-tier account, ME enforcing); if the legacy code
shows up, the legacy cap is biting first and needs raising on that environment.

### Timing and robustness

| Variable | Default | Meaning |
| --- | --- | --- |
| `RL_TEST_POLL_INTERVAL_S` | `2` | The ME's DB poll cadence; drives eject/book-sweep polling |
| `RL_TEST_EJECT_TIMEOUT_S` | `60` | Budget for eject/un-eject convergence and for the hook subprocess |
| `RL_TEST_MAX_BURST_ATTEMPTS` | `200` | Hard ceiling on burst loops so a misconfigured deployment can't spin |
| `RL_TEST_BUCKET_RECOVERY_S` | `burst x 60/rate x 1.5`, clamped to `[1, 30]` | Slept after every live test so a drained place bucket cannot poison the next one |
| `RL_TEST_PLACE_PACE_S` | `60/rate x 1.25` | Gap between paced creates, so a cap test reaches the cap without tripping the rate bucket |
| `RL_TEST_RETRY_AFTER_MAX_S` | `60` | Upper plausibility bound for `Retry-After` / `retryAfterMs` |
| `RL_TEST_RETRY_AFTER_SLACK_S` | `1` | Extra sleep added on top of `Retry-After` before asserting recovery |
| `RL_TEST_SETTLE_TIMEOUT_S` | `30` | Budget for `openOrders` to reflect a placement or a cancel |

### Eject hook (cross-repo)

`rl_ejected_accounts` lives in the off-chain Postgres, which this repo cannot
reach. The eject step is therefore delegated to two command templates supplied
by the localnet harness. Both must be set or `test_eject_flow.py` skips.

| Variable | Meaning |
| --- | --- |
| `RL_TEST_EJECT_CMD` | Command that ejects a wallet (one DB transaction: delete the `rl_whitelist` row, insert `rl_ejected_accounts` rows) |
| `RL_TEST_UNEJECT_CMD` | Command that reverses it (delete the eject rows, then re-insert the whitelist row — the P03 tool enforces that order) |

Both templates may use `{wallet}` and `{account_id}` placeholders. They are
formatted, then `shlex.split` and run **without a shell**, so no shell
metacharacters (pipes, `&&`, redirection, substitution) are interpreted — point
them at a script or a single binary invocation:

```bash
export RL_TEST_EJECT_CMD='/path/to/localnet/rl-eject.sh {wallet}'
export RL_TEST_UNEJECT_CMD='/path/to/localnet/rl-uneject.sh {wallet}'
```

A non-zero exit fails the test with the command's stderr attached. The test
always attempts the un-eject in a `finally`, so a mid-test failure does not
leave the account ejected.

## Findings (SDK, unmodified)

Recorded rather than patched — the SDK is regenerated from the spec, so the fix
belongs in the spec, not in a hand-edit.

1. **`ApiException.data` is `None` for 429 / 503 / 403.** The generated
   `_response_types_map` on the order-entry endpoints lists only
   `200` / `400` / `500`, so the new statuses are never deserialized. Nothing is
   lost — `ApiException.body` still holds the raw JSON and `ApiException.headers`
   still holds `Retry-After` — but callers must parse the body themselves.
   *Fix:* document `403` / `429` / `503` → `RequestError` on the order-entry
   endpoints in the spec, then regenerate.
2. **`RequestErrorCode` predates the v1 codes.** `NOT_WHITELISTED_ERROR`,
   `ACCOUNT_SUSPENDED_ERROR`, `CAPACITY_LIMITED_ERROR`,
   `OPEN_ORDER_COUNT_EXCEEDED_ERROR` and `OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR`
   are absent, so typed parsing of such a body raises. Fixed by regeneration.
3. **`RequestError` has no `retryAfterMs` field.** The value survives in the
   model's `additional_properties` bag, so nothing is dropped, but there is no
   typed accessor. Fixed by regeneration.
4. **`WsExecOperationError` drops `retryAfterMs`.** `sdk/reya_ws_exec/client.py`
   builds the exception from `code` / `message` / `request_id` only. Until it
   carries the field, ws-exec rate-limit assertions must read the raw envelope —
   which is why `test_ws_exec_parity.py` uses the raw harness.
   *Fix:* add `retry_after_ms` to `WsExecOperationError` (hand-written client,
   not generated) when the spec lands.

`tests/rate_limits/rl_errors.py` is written to work identically before and
after (1)-(3); it carries a `TODO(post-regen)` marking the tightening.
