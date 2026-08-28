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
state machine behind an injectable adapter. It currently has no CLI or live SDK adapter, so it
cannot submit an order. Offline tests prove these invariants:

- the profile, account, wallet, market, quantity, and both order notionals pass before adapter I/O;
- the order intent is post-only GTC, and modify restates that complete intent;
- REST and the wallet order-change stream must both prove initial, modified, and cancelled state;
- every external operation has a timeout;
- cleanup addresses only canonical order IDs returned to this run, never `close_all`, `cancelAll`,
  position flattening, or any pre-existing order; and
- mainnet mutation still requires the exact acknowledgement constant in addition to the profile.

## Next implementation slice

Implement the narrow SDK adapter and credential-free lifecycle evidence. Keep it unreachable from
the CLI until a local profile identifies the release, market, quantity/notional bounds, account,
wallet, and the live read-only probes have passed.
