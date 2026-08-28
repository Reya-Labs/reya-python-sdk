# Perp OB migration canary

This directory contains the fail-closed configuration layer for Linear PRO-657.
It is separate from the general live pytest suite because that suite has account-wide
order and position cleanup that is not safe for a mainnet cutover canary.

## Current scope

Static preflight and explicit read-only live probes exist. The runner does not load `.env`,
construct a trading client, use account credentials, or expose a mutation mode.

1. Copy the matching example profile into the ignored `canary/profiles/local/` directory.
2. Fill the exact release manifest, commander-approved market, hard bounds, account IDs,
   and wallet allowlist. Do not add credentials.
3. Set `enabled = true` only after those values have been reviewed.
4. Export the profile's `rpc_url_env` variable from the machine-local provider configuration.
   Do not put a credential-bearing RPC URL in the profile or shell history.
5. Run static preflight:

```bash
poetry run python scripts/run_canary.py \
  --profile canary/profiles/local/devnet1.toml \
  --preflight-only
```

Or prove the configured live target using only REST/RPC reads and WebSocket connect/close handshakes:

```bash
poetry run python scripts/run_canary.py \
  --profile canary/profiles/local/devnet1.toml \
  --probe-live-read-only
```

The runner validates the profile against the SDK's pinned environment identity and writes
a credential-free `artifacts/canary/<timestamp>-<environment>/preflight.json` record.
Devnet1 and the retired Cronos deployment share a chain ID, so the REST/read-WS/ws-exec
hosts and Orders Gateway must all match; chain ID alone never passes preflight. The live probe also
checks the designated market ID, calls `eth_chainId`, proves bytecode exists at the configured
Orders Gateway, and connects to both WebSocket surfaces without sending a frame.

Checked-in profiles are deliberately disabled and incomplete. Local profiles are ignored
because they contain run-specific account and wallet identifiers, though not secrets.

## Offline lifecycle engine

`scripts/canary_lifecycle.py` defines the bounded place → REST/WS visibility → modify → cancel
state machine. `scripts/canary_sdk_adapter.py` maps that state machine to the SDK's exact
post-only GTC create, modify, single-order cancel, and open-orders models. The adapter is
dependency-injected and is not constructed by the CLI, so the runner still cannot submit an order.
Offline tests prove these invariants:

- the profile, account, wallet, market, quantity, and both order notionals pass before adapter I/O;
- the order intent is post-only GTC, and modify restates that complete intent;
- REST and the wallet order-change stream must both prove initial, modified, and cancelled state;
- every external operation has a timeout;
- the REST client's chain, API URL, exchange, Orders Gateway, loaded market ID, owner wallet, and
  account must match the validated profile and plan;
- read WebSocket observations come only from the designated wallet channel, and a `FEED_RESET`
  event cannot be mistaken for a user cancellation;
- cleanup addresses only canonical order IDs returned to this run, never `close_all`, `cancelAll`,
  position flattening, or any pre-existing order; and
- mainnet mutation still requires the exact acknowledgement constant in addition to the profile;
  credential-free success and sanitized failure evidence are available for every lifecycle run.

## Recovery checkpoint and next slice

`scripts/canary_recovery.py` also defines an injected operator checkpoint. It snapshots the public
projection, pauses without owning any restart capability, then requires a fresh reconnect to prove
contiguous unique event sequences, projection convergence, and no order or position delta from the
baseline. Mainnet has a separate recovery-checkpoint acknowledgement. The CLI does not construct
this adapter or expose the checkpoint.

See `canary/ACCEPTANCE.md` for the ticket-to-code coverage map. The next implementation slice is
the controlled maker/taker match with run-owned position accounting and the operator-side evidence
contract for transaction receipts, canonical events/fills, database rows, and telemetry. Explicit
runtime orchestration remains blocked on reviewed local release, market, quantity/notional,
account, and wallet inputs. It must require read-only probes in the same run, subscribe to wallet
streams before entry, and retain separate mutation gates.
