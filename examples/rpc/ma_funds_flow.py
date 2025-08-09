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
    """
    Example script demonstrating the full flow of creating margin accounts,
    depositing rUSD, transferring funds between accounts, and withdrawing funds.
    """

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
