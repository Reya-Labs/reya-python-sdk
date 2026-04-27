#!/usr/bin/env node
// SPDX-License-Identifier: MIT
//
// EIP-712 signature parity harness — TS reference side.
//
// Reproduces the canonical signature bytes for three v2.3.0 envelopes (Order,
// OrderCancel, MassCancel) using ethers v6. Run from this directory:
//
//   npm install
//   node sign_ts.mjs
//
// Output is a JSON dict mapping {order, cancel, mass_cancel} → 0x-prefixed
// hex. The Python parity test (test_signature_parity.py) hardcodes these
// values and asserts the Python sign_* helpers produce the same bytes.
//
// Typed-data definitions and field semantics mirror
// /Users/ab/Code/reya-off-chain-monorepo/packages/common/src/transactions/sign.ts
// at commit feat/perpOB-8-candles. If those drift, regenerate by re-running
// this script and updating the expected hex in test_signature_parity.py.

import { Wallet } from "ethers";

// Fixed test vector — first hardhat well-known key. Address derives to
// 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266. Never use in production.
const PRIVATE_KEY =
  "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80";
const SIGNER_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266";

const CHAIN_ID = 89346162; // cronos / devnet1
const ORDERS_GATEWAY = "0x5a0ac2f89e0bdeafc5c549e354842210a3e87ca5"; // testnet OG proxy

const domain = {
  name: "Reya",
  version: "1",
  verifyingContract: ORDERS_GATEWAY,
  // chainId is intentionally absent — verifyingChainId travels in the envelope.
};

// === Order (on-chain-verified) ===
const orderTypes = {
  Order: [
    { name: "verifyingChainId", type: "uint256" },
    { name: "deadline", type: "uint256" },
    { name: "order", type: "OrderDetails" },
  ],
  OrderDetails: [
    { name: "accountId", type: "uint128" },
    { name: "marketId", type: "uint128" },
    { name: "exchangeId", type: "uint128" },
    { name: "orderType", type: "uint8" },
    { name: "quantity", type: "int256" },
    { name: "limitPrice", type: "uint256" },
    { name: "triggerPrice", type: "uint256" },
    { name: "timeInForce", type: "uint8" },
    { name: "clientOrderId", type: "uint64" },
    { name: "reduceOnly", type: "bool" },
    { name: "expiresAfter", type: "uint256" },
    { name: "signer", type: "address" },
    { name: "nonce", type: "uint256" },
  ],
};

// LIMIT IOC perp buy: 0.5 qty @ 3000 limit price.
const orderValue = {
  verifyingChainId: BigInt(CHAIN_ID),
  deadline: BigInt(1745000000),
  order: {
    accountId: 12345n,
    marketId: 1n, // ETH perp
    exchangeId: 2n, // Reya DEX id
    orderType: 0, // LIMIT
    quantity: BigInt("500000000000000000"), // +0.5 E18 (signed; positive = buy)
    limitPrice: BigInt("3000000000000000000000"), // 3000 E18
    triggerPrice: 0n,
    timeInForce: 1, // IOC
    clientOrderId: 42n,
    reduceOnly: false,
    expiresAfter: 0n,
    signer: SIGNER_ADDRESS,
    nonce: BigInt(1700000000000000),
  },
};

// === OrderCancel (matching-engine layer) ===
const orderCancelTypes = {
  OrderCancel: [
    { name: "verifyingChainId", type: "uint64" },
    { name: "deadline", type: "uint64" },
    { name: "cancel", type: "OrderCancelDetails" },
  ],
  OrderCancelDetails: [
    { name: "accountId", type: "uint64" },
    { name: "marketId", type: "uint64" },
    { name: "orderId", type: "uint64" },
    { name: "clOrdId", type: "uint64" },
    { name: "nonce", type: "uint64" },
  ],
};

const orderCancelValue = {
  verifyingChainId: BigInt(CHAIN_ID),
  deadline: BigInt(1745000060),
  cancel: {
    accountId: 12345n,
    marketId: 1n,
    orderId: BigInt("63552420354981888"),
    clOrdId: 0n,
    nonce: BigInt(1700000000000001),
  },
};

// === MassCancel (matching-engine layer) ===
const massCancelTypes = {
  MassCancel: [
    { name: "verifyingChainId", type: "uint64" },
    { name: "deadline", type: "uint64" },
    { name: "massCancel", type: "MassCancelDetails" },
  ],
  MassCancelDetails: [
    { name: "accountId", type: "uint64" },
    { name: "marketId", type: "uint64" },
    { name: "nonce", type: "uint64" },
  ],
};

const massCancelValue = {
  verifyingChainId: BigInt(CHAIN_ID),
  deadline: BigInt(1745000120),
  massCancel: {
    accountId: 12345n,
    marketId: 0n, // 0 = all markets (matches TS SDK ?? 0 fallback)
    nonce: BigInt(1700000000000002),
  },
};

const wallet = new Wallet(PRIVATE_KEY);

const orderSig = await wallet.signTypedData(domain, orderTypes, orderValue);
const cancelSig = await wallet.signTypedData(
  domain,
  orderCancelTypes,
  orderCancelValue,
);
const massCancelSig = await wallet.signTypedData(
  domain,
  massCancelTypes,
  massCancelValue,
);

console.log(
  JSON.stringify(
    {
      signer_address: SIGNER_ADDRESS,
      chain_id: CHAIN_ID,
      orders_gateway: ORDERS_GATEWAY,
      signatures: {
        order: orderSig,
        order_cancel: cancelSig,
        mass_cancel: massCancelSig,
      },
    },
    null,
    2,
  ),
);
