# Live liquidation test fixture

Prepare two fresh rUSD-only perp accounts owned by the same wallet, then open
one long and one short near liquidation margin. All commands are read-only
unless `open --execute` or `setup --execute` is supplied. The `create` and `fund` subcommands
**only emit unsigned transactions** for the owner to review and submit; the
separate `setup --execute` command automates the complete setup locally.

Core still checks liquidation margin at settlement after ME admission is
bypassed. This tool therefore requires a **positive USD buffer above LMR**;
it cannot open positions already below LMR. A subsequent adverse price move
can make one account liquidatable. Opposite positions do not both become
liquidatable from the same price move.

## Environment

Run from this worktree with its Python 3.12+ environment:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m examples.liquidation_test --help
```

The default owner is `0x48D677a576fC9010f73e0413513f1A8817EAc7eC`.
Override it with `--owner ADDRESS` before the subcommand.
RPC and API are fixed to Reya mainnet, chain 1729.

Do not use the SDK checkout's implicit `.env`; it may point to devnet.
Use an explicit, private env file with `CHAIN_ID=1729`,
`PERP_WALLET_ADDRESS_1` matching the owner, and `PERP_PRIVATE_KEY_1`.
`--key-var` supports another key variable name. Inspection prints only the
public signer address, gas balance, and permissions; it does not sign anything.

```bash
.venv/bin/python -m examples.liquidation_test inspect --env-file "$SIGNER_ENV"
```

OrdersGateway authorization permits order signing. Funding requires the
owner or a separately authorized Core account user; gateway permission alone
is insufficient. The commands below emit transactions with the owner as sender.

## One-command account setup (operator runs locally)

This creates **two new perp accounts**, initializes each account's collateral
pool through Core market 1 (ETH; no position is opened), and transfers all
available rUSD from account 135145 into those two accounts in equal halves.
It reads token-native `getCollateralInfo.realBalance`, not net deposits or a
USD-valued estimate. Any odd micro-rUSD remains in the source.

The workstation virtual environment is already prepared. Run:

```bash
cd /workspace/daniel_reya_xyz/dev/reya-python-sdk-liquidation-test
.venv/bin/python -m examples.liquidation_test.setup \
  --source-account 135145 \
  --trade-env-file /workspace/daniel_reya_xyz/dev/reya-python-sdk/.git/reya-prod.env \
  --state-dir /home/daniel_reya_xyz/.local/state/reya-liquidation-setup-135145 \
  --execute
```

**This sends five mainnet transactions**: create and activate each account,
then one atomic transaction containing the two equal funding transfers. It
prompts privately in your terminal for the **owner wallet's private key**.
The existing trading delegate key cannot activate fresh accounts or fund them.
The owner wallet needs native gas. The key is never saved or accepted as a
command-line argument. Do not paste it into chat. The existing production env
is used only to verify that the trading delegate can subsequently trade for
this owner. The script does not alter permissions or open any positions.

Remove `--execute` for read-only preflight and the expected split. The command
stops if the source has positions/open orders, collateral other than rUSD,
stale Core prices, or unavailable account APIs. Before funding, both new
accounts must be empty and their collateral pools must match the source.
`--activation-market-id` changes the initialization market if needed; it does
not select or trade the eventual position market.

The output reports `long_account`, `short_account`, `rusd_per_account`, and the
state file. Use those IDs and balance with the `open` command below.

For interruption or timeout, **rerun exactly the same command with the same
state directory**. The state records transaction hashes before broadcasting,
recovers account IDs from receipts, and never sends a recorded transaction
again. It resumes incomplete setup and checks completed funding without
repeating it. Keep the state directory after completion. An ambiguous send
that was never mined, or a reverted transaction, needs manual reconciliation;
starting a new state directory can create duplicates. Avoid other activity on
the owner wallet or these accounts during setup.

## Account preparation (operator submits transactions)

Create two new perp accounts so existing collateral and exposure remain isolated:

```bash
.venv/bin/python -m examples.liquidation_test create --count 2 > create-accounts.json
```

Submit both unsigned transactions from the owner wallet (with distinct wallet
nonces), wait for receipts, and get the two new IDs from creation events or
`inspect`. Transaction objects include a human-readable `description`; remove
that field before passing them to a wallet RPC. The wallet supplies gas and nonce.
Never guess the new account IDs from the global counter. Before `fund`, also
initialize each fresh account with the owner-signed Core call
`activateFirstMarketForAccount(accountId, 1)`; `setup --execute` handles this
automatically. Market 1 associates the ETH collateral pool without a trade.

Choose `SOURCE_ID`, `LONG_ID`, `SHORT_ID`, and `COLLATERAL_PER_ACCOUNT` yourself:

```bash
.venv/bin/python -m examples.liquidation_test fund \
  --source-account "$SOURCE_ID" --long-account "$LONG_ID" --short-account "$SHORT_ID" \
  --amount "$COLLATERAL_PER_ACCOUNT" > fund-accounts.json
```

This simulates one atomic Core transaction containing two transfers, and prints
its unsigned calldata. It requires two empty destinations, a separate flat
source, sufficient current on-chain rUSD, and amounts with at most six decimals.
Submit through the owner wallet, wait for settlement, and inspect again.
Do not rerun `create` or submit a saved funding transaction without reconciling
receipts and account balances first.

## Position planning and operator execution

Choose the market, positive USD margin buffer, maximum notional per account,
and slippage limit. The following command does not load keys or send orders:

```bash
.venv/bin/python -m examples.liquidation_test open \
  --long-account "$LONG_ID" --short-account "$SHORT_ID" --symbol "$SYMBOL" \
  --max-collateral "$COLLATERAL_PER_ACCOUNT" --max-notional "$MAX_NOTIONAL" \
  --margin-buffer "$MARGIN_BUFFER_USD" --slippage-bps "$SLIPPAGE_BPS"
```

The planner requires cutover `markPrice` and order-book depth feeds no older
than five seconds, checks ownership and absence of existing exposure/orders,
and rejects non-rUSD collateral. It uses current on-chain margin balance,
the market liquidation margin parameter, the largest published taker fee,
and the signed worst fill price. Quantity is rounded down to a valid lot;
price is rounded outward to a valid tick (at most one extra tick beyond the
specified slippage). If the near-LMR quantity exceeds the notional cap or
visible liquidity, the planner stops. It does not silently use a smaller size.

For a flat account, the estimate is:

```
LMR = quantity * mark * liquidation_margin_parameter
MB_after = balance - quantity * (direction * (fill - mark) + fill * fee)
quantity = floor_to_lot((balance - buffer) /
           (mark * liquidation_margin_parameter + direction * (limit - mark) + limit * fee))
```

Direction is +1 for the long and -1 for the short. Better fills or fee discounts
leave more margin than the estimate. Price, funding, parameter and settlement
changes can move the actual buffer. The tool reports settled Core margin;
it does not top up positions automatically to chase a tighter target.

After independently verifying the intended ME image is running and reviewing
the plan, the operator can append the following to the same command:

```bash
  --execute --env-file "$SIGNER_ENV" --journal /absolute/path/to/new-batch.jsonl
```

This submits real mainnet IOC orders, long first, then short. Each leg is
replanned immediately before submission. The first must fully fill and settle
before the second is sent. The orders are **not atomic**: the first may remain
open if the second fails, and opposite notionals need not match. Settlement is
confirmed using Core exposure and the API position; the default timeout is
60 seconds. It stops on partial fills, rejected orders, transport errors,
unconfirmed settlement, or observed non-positive liquidation delta.

There is no submission retry. A timeout is ambiguous and may mean the order
was accepted. Inspect the journal, order status, positions and Core state before
any manual recovery. An existing journal path refuses a rerun. The journal
records order identifiers and results, never keys or signatures. It does not
close trades, deploy the ME, or change liquidation settings.

## Verification

```bash
.venv/bin/pytest -q tests/validation/test_liquidation_test_script.py
```

Tests are offline: long/short sizing, fee/slippage math, invalid budgets and
liquidity, unsigned transfer encoding, default read-only behavior, stale/legacy
feed refusal, key validation, and stopping before the second leg on failures.
