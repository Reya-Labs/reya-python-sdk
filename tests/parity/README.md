# EIP-712 signature parity (TS ↔ Py)

Confirms the Python `sign_order` / `sign_cancel_order` / `sign_mass_cancel`
helpers produce byte-identical signatures to the canonical TypeScript impl in
[`reya-off-chain-monorepo/packages/common/src/transactions/sign.ts`](https://github.com/Reya-Labs/reya-off-chain-monorepo/blob/feat/perpOB/packages/common/src/transactions/sign.ts).

## How it works

- [sign_ts.mjs](sign_ts.mjs) signs three v2.3.0 envelopes (Order, OrderCancel,
  MassCancel) with a fixed hardhat test key + fixed payload using ethers v6's
  `signTypedData`. Outputs a JSON dict of `{order, order_cancel, mass_cancel}` →
  hex signatures.
- [test_signature_parity.py](test_signature_parity.py) hardcodes those hex
  values and asserts the Python helpers produce them for the same inputs.

A drift in either direction (Python helper or canonical TS impl) breaks the
test loudly.

## Running the Python side

From the SDK repo root, in the poetry env:

```bash
poetry run pytest tests/parity/test_signature_parity.py -v
```

## Regenerating the expected hex (when TS evolves)

```bash
cd tests/parity
npm install   # one-time; pulls ethers v6 into ./node_modules
node sign_ts.mjs
```

Copy the three hex strings from the output into `EXPECTED_SIGNATURES` in
[test_signature_parity.py](test_signature_parity.py).

## Test vector

- Private key: `0xac09…ff80` (first hardhat well-known key — never use in
  production)
- Signer address: `0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266`
- Chain id: `89346162` (cronos / devnet1)
- OrdersGateway: `0x5a0ac2f89e0bdeafc5c549e354842210a3e87ca5`
- Order: LIMIT IOC perp buy, 0.5 qty @ $3000, account 12345, market 1, exchange 2
- OrderCancel: targets a specific order_id on the same account/market
- MassCancel: market_id=0 (cancel-all-markets, matching the TS SDK's
  `params.marketId ?? 0` fallback in `massCancelMEOrders`)
