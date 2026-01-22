"""
Margin Account Funds Flow - Create accounts, deposit, transfer, and withdraw funds.

This script demonstrates the full flow of creating margin accounts,
depositing rUSD, transferring funds between accounts, and withdrawing funds.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- PERP_PRIVATE_KEY_1: Your Ethereum private key

Usage:
    python -m examples.rpc.ma_funds_flow
"""

from sdk.reya_rpc import (
    DepositParams,
    TransferParams,
    WithdrawParams,
    create_account,
    deposit,
    get_config,
    transfer,
    withdraw,
)


def main():
    """Execute margin account funds flow example."""

    # Load configuration
    config = get_config()

    # Create two new margin accounts
    a = create_account(config)["account_id"]
    b = create_account(config)["account_id"]

    # Define the amount in rUSD (scaled by 10^6)
    amount_e6 = int(1e6)

    # Deposit rUSD into the first margin account (account A)
    deposit(config, DepositParams(account_id=a, amount=amount_e6))

    # Transfer rUSD from account A to account B
    transfer(config, TransferParams(account_id=a, amount=amount_e6, to_account_id=b))

    # Withdraw rUSD from account B back to the user's wallet
    withdraw(config, WithdrawParams(account_id=b, amount=amount_e6))


if __name__ == "__main__":
    main()
