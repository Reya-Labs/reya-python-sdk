from reya_actions.actions import create_account, deposit, withdraw, transfer
from reya_actions.actions import DepositParams, WithdrawParams, TransferParams
from reya_actions import get_config

def main():
    config = get_config()

    a = create_account(config)
    b = create_account(config)

    amount = int(1e6)
    deposit(config, DepositParams(account_id=a, amount=amount))
    transfer(config, TransferParams(account_id=a, amount=amount, to_account_id=b))
    withdraw(config, WithdrawParams(account_id=b, amount=amount))


if __name__ == "__main__":
    main()
