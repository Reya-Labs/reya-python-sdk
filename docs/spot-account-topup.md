# Spot Account Top-Up Process (devnet1)

How to create and fund spot accounts on devnet1, the perpOB testnet, using Foundry's `cast` tool.

devnet1 runs on chain `89346162` and reuses that chain's rUSD and WETH tokens, but has its own Core Proxy. Use the Core Proxy below; a Core address from older docs or configs funds a different deployment.

## Prerequisites

- **Foundry** installed (`cast` CLI available)
- Wallet(s) with private keys holding rUSD and WETH tokens on chain `89346162`
- Wallet(s) must have native gas tokens (ETH) for transaction fees

## Contract Addresses (devnet1)

| Contract | Address | Decimals |
|----------|---------|----------|
| Core Proxy | `0xC33D0A4FC05aF98447126f1680cA7316de29e5d4` | — |
| rUSD | `0x9DE724e7b3facF87Ce39465D3D712717182e3e55` | 6 |
| WETH | `0x2CF56315ACC7E791B1A0135c09d8D5C8dBCD2F14` | 18 |

| Setting | Value |
|---------|-------|
| RPC URL | `https://rpc-reya-cronos.t.conduit.xyz/<API_KEY>` |
| Chain ID | `89346162` |

## Step 1: Check Wallet Balances

Check token balances to ensure the wallet holds enough tokens to deposit:

```bash
# rUSD balance (result is in 6-decimal raw units; divide by 1e6 for human-readable)
cast call <RUSD_ADDRESS> "balanceOf(address)(uint256)" <WALLET> --rpc-url <RPC_URL>

# WETH balance (result is in 18-decimal raw units; divide by 1e18)
cast call <WETH_ADDRESS> "balanceOf(address)(uint256)" <WALLET> --rpc-url <RPC_URL>

# Native gas balance
cast balance <WALLET> --rpc-url <RPC_URL>
```

> **Important:** If the wallet has zero native gas balance, you must send some from another funded wallet before it can execute any transactions:
> ```bash
> cast send <TARGET_WALLET> --value 50000000000000000 \
>   --rpc-url <RPC_URL> --chain-id 89346162 --private-key <FUNDED_WALLET_PK>
> ```

## Step 2: Create a Spot Account (if needed)

Call `createOrGetSpotAccount` on the Core Proxy. This is **idempotent** — it returns the existing spot account ID if one already exists for the wallet.

```bash
cast send <CORE_PROXY> \
  "createOrGetSpotAccount(address)" \
  <WALLET_ADDRESS> \
  --rpc-url <RPC_URL> \
  --chain-id 89346162 \
  --private-key <PRIVATE_KEY>
```

Then read back the spot account ID:

```bash
cast call <CORE_PROXY> \
  "getOwnerSpotAccountId(address)(uint128)" \
  <WALLET_ADDRESS> \
  --rpc-url <RPC_URL>
```

Update the `.env` file with the returned `SPOT_ACCOUNT_ID`.

## Step 3: Approve + Deposit Tokens

Each deposit requires two transactions: an ERC-20 `approve` followed by the Core Proxy `deposit`.

### Deposit rUSD

```bash
# Approve (e.g. 500 rUSD = 500000000 in 6 decimals)
cast send <RUSD_ADDRESS> \
  "approve(address,uint256)" \
  <CORE_PROXY> <AMOUNT_RAW> \
  --rpc-url <RPC_URL> --chain-id 89346162 --private-key <PK>

# Deposit into spot account
cast send <CORE_PROXY> \
  "deposit(uint128,address,uint256)" \
  <SPOT_ACCOUNT_ID> <RUSD_ADDRESS> <AMOUNT_RAW> \
  --rpc-url <RPC_URL> --chain-id 89346162 --private-key <PK>
```

### Deposit WETH

```bash
# Approve (e.g. 0.2 WETH = 200000000000000000 in 18 decimals)
cast send <WETH_ADDRESS> \
  "approve(address,uint256)" \
  <CORE_PROXY> <AMOUNT_RAW> \
  --rpc-url <RPC_URL> --chain-id 89346162 --private-key <PK>

# Deposit into spot account
cast send <CORE_PROXY> \
  "deposit(uint128,address,uint256)" \
  <SPOT_ACCOUNT_ID> <WETH_ADDRESS> <AMOUNT_RAW> \
  --rpc-url <RPC_URL> --chain-id 89346162 --private-key <PK>
```

## Common Amounts Reference

| Token | Human Amount | Raw Amount |
|-------|-------------|------------|
| rUSD | 500 | `500000000` |
| rUSD | 50 | `50000000` |
| WETH | 0.2 | `200000000000000000` |
| WETH | 0.1 | `100000000000000000` |

## Notes

- The `deposit` function transfers tokens from the **wallet** (msg.sender) into the on-chain **margin account**. The wallet must hold the ERC-20 balance.
- `createOrGetSpotAccount` is idempotent — safe to call even if the account already exists.
- Each wallet can only have **one** spot account. The account ID is deterministic per wallet.
- Spot test minimum requirements: ≥ 0.05 ETH and ≥ 15 rUSD per account. Deposit more for headroom.
- If a wallet runs out of native gas, send more from a funded wallet (see Step 1 note).
