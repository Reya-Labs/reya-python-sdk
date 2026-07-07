# Track B Local SDK E2E

This worktree can run the devnet-style SDK integration suite against the
sibling all-local Track B stack.

## Entry Point

```bash
make e2e PYTEST_ARGS=-rxXs
```

`make e2e` invokes `scripts/track_b_env.sh` before pytest and fails closed when
the sibling backend address file is missing or incomplete. When the address file
exists at `../offchain/e2e/out/compose/basic-addresses.env`, the wrapper
force-exports local API, read WebSocket, ws-exec, chain id, OrdersGateway, and
deterministic perp/spot account env vars so stale devnet shell exports cannot
leak into Track B evidence.

To point at a non-default address file:

```bash
TRACK_B_ADDRESS_ENV=/path/to/basic-addresses.env make e2e PYTEST_ARGS=-rxXs
```

For a deliberately preconfigured external/devnet environment, use:

```bash
make e2e-configured PYTEST_ARGS=-rxXs
```

## Required Visibility

Always keep skip and xfail reasons visible for Track B evidence:

```bash
make e2e PYTEST_ARGS=-rxXs
```

Focused reruns should use the same wrapper, for example:

```bash
bash scripts/track_b_env.sh poetry run pytest -q -rxXs tests/perp/test_limit_orders.py
```

Spot and ws-exec tests are in scope. Missing `SPOT_*`, `PERP_*`,
`REYA_WS_EXEC_URL`, market definitions, or balances should fail loudly rather
than silently skipping local coverage.

## Latest Evidence

Evidence bundle:

```bash
../offchain/e2e/out/pr-readiness-2026-07-07T0525Z/evidence.md
```

SDK SHA tested: `2c5ca578ec68e5584868abb450362680f7cafc64`
(`origin/feat/perpOB`, after PR #63).

Focused gate:

```bash
make e2e \
  E2E_TEST_PATHS="tests/perp/test_limit_orders.py tests/perp/test_market_data.py tests/api_contract/test_api_validation.py tests/spot tests/ws_exec tests/engine/test_order_history.py" \
  PYTEST_ARGS="-q -rxXs"
# 108 passed, 2 skipped in 48.24s
```

Full gate:

```bash
make e2e PYTEST_ARGS=-rxXs
# 331 passed, 13 skipped, 1 xfailed in 551.96s (0:09:11)
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
