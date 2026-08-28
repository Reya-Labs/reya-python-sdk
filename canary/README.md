# Perp OB migration canary

This directory contains the fail-closed configuration layer for Linear PRO-657.
It is separate from the general live pytest suite because that suite has account-wide
order and position cleanup that is not safe for a mainnet cutover canary.

## Current scope

Only static, non-mutating preflight exists. The runner does not load `.env`, construct
an SDK client, make network requests, or expose a mutation mode.

1. Copy the matching example profile into the ignored `canary/profiles/local/` directory.
2. Fill the exact release manifest, commander-approved market, hard bounds, account IDs,
   and wallet allowlist. Do not add credentials.
3. Set `enabled = true` only after those values have been reviewed.
4. Run:

```bash
poetry run python scripts/run_canary.py \
  --profile canary/profiles/local/devnet1.toml \
  --preflight-only
```

The runner validates the profile against the SDK's pinned environment identity and writes
a credential-free `artifacts/canary/<timestamp>-<environment>/preflight.json` record.
Devnet1 and the retired Cronos deployment share a chain ID, so the REST/read-WS/ws-exec
hosts and Orders Gateway must all match; chain ID alone never passes preflight.

Checked-in profiles are deliberately disabled and incomplete. Local profiles are ignored
because they contain run-specific account and wallet identifiers, though not secrets.

## Next implementation slice

Add read-only live identity probes, followed by a run-scoped place → REST/WS visibility →
modify → cancel scenario. The scenario must track and clean up only order IDs created by
that run. It must not use the existing account-wide `close_all` or position-flatten fixtures.
