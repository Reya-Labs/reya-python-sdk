"""
Bridge In and Deposit - Bridge USDC from Arbitrum and deposit to margin account.

This script demonstrates how to bridge USDC into Reya Network from Arbitrum
and optionally deposit it into a margin account once transfer is completed.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- PERP_ACCOUNT_ID_1: Your Reya margin account ID
- PERP_PRIVATE_KEY_1: Your Ethereum private key

Usage:
    python -m examples.rpc.bridge_in_and_deposit
"""

import os

from dotenv import load_dotenv

from sdk.reya_rpc import BridgeInParams, DepositParams, bridge_in_from_arbitrum, deposit, get_config


def main():
    """Execute bridge in and deposit operation."""

    # Load environment variables from a .env file
    load_dotenv()

    # Retrieve the margin account ID from environment variables
    account_id = int(os.environ["PERP_ACCOUNT_ID_1"])

    # Load configuration
    config = get_config()

    # Define the deposit amount in USDC (scaled by 10^6)
    amount_e6 = int(1e6)

    # Execute bridging of USDC from Arbitrum to Reya Network
    bridge_only = True
    if bridge_only:
        result = bridge_in_from_arbitrum(config, BridgeInParams(amount=amount_e6, fee_limit=int(0.01e18)))
        tx_hash = result["transaction_receipt"].transactionHash.hex()
        print(f"Check status of bridging transfer here: https://www.socketscan.io/tx/{tx_hash}")

    # Deposit rUSD into the margin account after bridging is completed
    transfer_arrived = False
    if transfer_arrived:
        deposit(config, DepositParams(account_id=account_id, amount=amount_e6))


if __name__ == "__main__":
    main()
