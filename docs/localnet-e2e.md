# Reya Localnet SDK E2E

Reya Localnet orchestration is owned by `reya-off-chain-monorepo`. This SDK
worktree owns the public live integration tests and their no-silent-skip
behavior; it no longer hardcodes sibling compose output or Localnet fixture
paths.

## Entry Point

From `reya-off-chain-monorepo`, run:

```bash
make localnet-sdk LOCALNET_PYTEST_ARGS=-rxXs
```

That target loads `e2e/out/compose/basic-addresses.env`, maps deterministic
Localnet accounts/endpoints into the SDK env, and invokes this repo's `make e2e`.

From this repo, `make e2e` expects the live environment to already be configured:

```bash
make e2e PYTEST_ARGS=-rxXs
```

## Required Visibility

Always keep skip and xfail reasons visible for Localnet evidence:

```bash
make -C ../reya-off-chain-monorepo localnet-sdk LOCALNET_PYTEST_ARGS=-rxXs
```

Focused reruns can override paths through the offchain target, for example:

```bash
make -C ../reya-off-chain-monorepo localnet-sdk-focused \
  LOCALNET_PYTEST_ARGS="-q -rxXs"
```

Spot and ws-exec tests are in scope. Missing `SPOT_*`, `PERP_*`,
`REYA_WS_EXEC_URL`, market definitions, or balances should fail loudly rather
than silently skipping local coverage.

## Latest Evidence

Durable Localnet evidence lives in the offchain PR #2768 review packet and the
offchain `e2e/README.md` evidence policy. Run-specific logs and artifacts should
come from scheduled-CI artifacts, PR comments, or the work-order history rather
than dated local `e2e/out/...` paths.

SDK SHA tested: `2c5ca578ec68e5584868abb450362680f7cafc64`
(`origin/feat/perpOB`, after PR #63).

Focused gate, through offchain:

```bash
make localnet-sdk-focused \
  LOCALNET_PYTEST_ARGS="-q -rxXs"
# 108 passed, 2 skipped in 48.24s
```

Full gate:

```bash
make localnet-sdk LOCALNET_PYTEST_ARGS=-rxXs
# Latest structure-pass rerun: 331 passed, 13 skipped, 1 xfailed in 540.95s (0:09:00)
```

Skip/xfail classification:

- 10 `PRO-226` trigger-queue product-gap skips in
  `tests/perp/test_trigger_orders.py`.
- 2 ws-exec TP/SL product-gap skips in `tests/ws_exec/test_ws_exec.py`.
- 1 controlled-book precondition skip in
  `tests/engine/test_modify_ws_exec.py`.
- 1 `PRO-475` xfail in
  `tests/engine/test_modify_validation.py::test_trigger_order_not_modifiable`.
- 0 env-gated spot/ws-exec skips.
