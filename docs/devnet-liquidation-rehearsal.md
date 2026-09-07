# Keyrock liquidation rehearsal

Devnet only. This deliberately changes the ETH market's published mark price for
up to 60 seconds. It can affect other devnet ETH accounts and SL/TP triggers.
Use only during an explicitly authorized rehearsal window.

## Setup

- ME branch: `feat/devnet-mark-price-override`, based on main v4.3.0,
  [reya-chain #244](https://github.com/Reya-Labs/reya-chain/pull/244).
- Enable `MATCHING_ENGINE__MANUAL_MARK_PRICE_OVERRIDES__ENABLED=true` in devnet1.
  No manual price is active merely because the feature is enabled.
- SDK worktree: `reya-python-sdk/.worktrees/devnet-liquidation-rehearsal`.
  Its `.env` symlinks to the shared SDK devnet env; do not copy keys into scripts.
- Victim: account 9. Maker: account 11. Keyrock Dutch/backstop recipient: 372.
- Victim funding source: its own spot account 10000000006. `fund` tops a flat
  victim up to exactly 400 rUSD; it does not mint or touch Keyrock's credentials.

All commands below run from that SDK worktree. Omit `--execute` to inspect the
plan without sending an order, transaction, or mark-price control command.

## Rehearse

```sh
.venv/bin/python -m scripts.devnet_liquidation_rehearsal status
.venv/bin/python -m scripts.devnet_liquidation_rehearsal fund --execute
```

Check existing positions and Keyrock capacity before selecting the victim side.
The default is 3.8 ETH **long**, which offsets Keyrock's existing short inventory
from September 2. Consequently, the liquidation receipts show acquisitions but
Keyrock's net short shrinks rather than a second position appearing.

Pause the local devnet perp market-maker only for the controlled opening fill.
The service uses account 10 through explicit environment overrides. Preserve its
previous running/stopped state; do not start it if it was already stopped.
Use STOP/CONT signals, not `systemctl stop`: this is a transient unit that can
disappear when stopped. Its existing quotes remain in the book while paused;
the rehearsal chooses a better price inside that spread to isolate its fill.
The spot market-maker does not need to be stopped.

```sh
(
  if systemctl --user is-active --quiet reya-mm-perp-devnet.service; then
    trap 'systemctl --user kill --kill-whom=main --signal=SIGCONT reya-mm-perp-devnet.service' EXIT
    systemctl --user kill --kill-whom=main --signal=SIGSTOP reya-mm-perp-devnet.service
  fi
  .venv/bin/python -m scripts.devnet_liquidation_rehearsal open --qty 3.8 --side long --execute
)

.venv/bin/python -m scripts.devnet_liquidation_rehearsal dutch --execute
.venv/bin/python -m scripts.devnet_liquidation_rehearsal backstop --execute

.venv/bin/python -m scripts.devnet_liquidation_rehearsal status
.venv/bin/python -m scripts.devnet_liquidation_rehearsal fund --execute
```

Restore the bot even if opening fails. Do not blindly retry an uncertain fill;
inspect on-chain bases, execution busts and open orders first. A successful open
requires both accounts' on-chain position deltas, not just an order acknowledgment.

Each liquidation command recalculates its target from live chain margin, LMR and
mark price. It refuses targets outside 4.3% of fresh Stork prices (the engine's
hard bound is 4.5%). It clears the override in `finally`, whether it succeeds or
fails. There is no price-override refresh loop. A lost process is bounded by the
engine's 60-second TTL. These bounds mean an arbitrary stale/open position is not
guaranteed to be liquidatable: the preflight must pass at call time.

Success checks exact `PassivePerpExecutionV3` receipts for account 9 against 372:

| Stage | V3 executionType | Expected result |
|---|---:|---|
| Dutch | 1 | Partial victim reduction, equal inventory received by Keyrock |
| Backstop | 3 | Victim flat, remaining inventory received by Keyrock |

V3 **ADL is 4**, not 3. Core's separate `LiquidationType` enum uses Backstop=2;
do not confuse that enum with V3 executionType. The REST API may group both tiers
under `type: LIQUIDATION`; it is not sufficient evidence of the exact tier.

## Leave ready for the next call

Leave the override-capable image deployed, with **no active override**, account 9
flat and funded to 400 rUSD, no rehearsal resting orders, and the market-maker
restored. Open the new victim position during the call, not overnight.

Emergency clear (also safe when nothing is active):

```sh
.venv/bin/python -m scripts.devnet_liquidation_rehearsal clear --execute
```

Full feature rollback: remove the manual-override enable env and restore the
pre-rehearsal image
`europe-west3-docker.pkg.dev/mainnet-473609/reya/reya-chain@sha256:6f2473b1b89646d6e78cb058f28db4eca615d6583e98ebb7b688664288b4f2d5`.
Preserve both persistence `RESET_STATE=false` settings. Rollback restarts the ME;
ordinary clearing does not.

Offline validation:

```sh
.venv/bin/python -m unittest scripts.test_devnet_liquidation_rehearsal
```

## Verified September 7, 2026, 23:16 UTC

Deployed image digest:
`sha256:47cb3eb500404a30e03ce0fb91030d6d6829c8854036072cbe43b98052b91d23`.
Source commit: `6ef453ee82680ea0fdc939593e1215066a59bb12`, based on main `97f9c7a6`.
Local Rust formatting, Clippy, all-features tests and no-default-features tests
passed. Fresh live opening fill and both liquidation tiers settled successfully.

| Stage | Victim base before → after | Keyrock acquisition | V3 type | Sequence |
|---|---|---|---:|---:|
| Dutch, mark 2453.076 | 3.8 → 2.691 ETH | 1.109 ETH long | 1 | 121015 |
| Backstop, mark 2406.067 | 2.691 → 0 ETH | 2.691 ETH long | 3 | 121016 |

Dutch transaction:
`0x44dfbd6472e08f52c16c91977fbf50e331590dbea8f52331d2c8c5615984a46a`.
Backstop transaction:
`0xadc9497a0a486c675398edeb8738535d4d1b39118eeb119e9d474104ab1d441b`.
Both also appeared in Keyrock's
[perpExecutions](https://api-devnet.reya-cronos.network/v2/wallet/0xB89F0700dc6D92f715325d460aAF87a1640F629B/perpExecutions).
Keyrock's net short became 3.4 ETH (from 7.2). Account 11's long became 3.714 ETH.

The override was cleared after each stage. The victim was re-funded to exactly
400 rUSD from its own spot account, in transaction
`0x2696c68b663d270f40c0900fbcbd0335e64685b655dcaf89ae72e474947e35b5`.
Both market-maker services were restored to active state.

Deployment reconciliation is in
[reya-devops #1062](https://github.com/Reya-Labs/reya-devops/pull/1062).
The live rollout changed only the ME image and the override-enable env, preserving
existing unrelated deployment settings. Until that PR is merged, a manual ArgoCD
sync of the old configuration would remove the rehearsal feature. Automatic sync
and self-heal are disabled for this workload; do not sync the old config before
the call.
