import os

from dotenv import load_dotenv

from sdk.reya_rpc import bridge_out_to_arbitrum, get_config, withdraw
from sdk.reya_rpc.actions import BridgeOutParams, WithdrawParams


def main():
    """
    Example script demonstrating how to withdraw rUSD from a margin account
    and bridge it out from Reya Network to Arbitrum.
    """

    # Load environment variables from .env file
    load_dotenv()

    # Retrieve the margin account ID from environment variables
    account_id = int(os.environ["ACCOUNT_ID"])

    # Load configuration
    config = get_config()

    # Define the withdrawal amount in rUSD (scaled by 10^6)
    amount_e6 = int(1e6)

    # Withdraw rUSD from the margin account to the user's wallet
    withdraw(config, WithdrawParams(account_id=account_id, amount=amount_e6))

    # Bridge out rUSD from Reya Network to Arbitrum
    result = bridge_out_to_arbitrum(config, BridgeOutParams(amount=amount_e6, fee_limit=int(0.01e18)))
    tx_hash = result["transaction_receipt"].transactionHash.hex()
    print(f"Check status of bridging transfer here: https://www.socketscan.io/tx/{tx_hash}")


if __name__ == "__main__":
    main()
