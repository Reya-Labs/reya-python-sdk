from reya_actions.actions import DepositParams, deposit
from reya_actions import get_config
import os
from dotenv import load_dotenv
from reya_actions.actions.bridge_in import BridgeInParams, bridge_in_from_arbitrum

def main():
    load_dotenv()
    account_id = int(os.environ['ACCOUNT_ID'])
    config = get_config()

    amount_e6 = int(1e6)

    bridge_only = True
    if bridge_only:
        result = bridge_in_from_arbitrum(config, BridgeInParams(amount=amount_e6, fee_limit=int(0.01e18)))
        tx_hash = result['transaction_receipt'].transactionHash.hex()
        print(f'Check status of bridging transfer here: https://www.socketscan.io/tx/{tx_hash}')

    transfer_arrived = False
    if transfer_arrived:
        deposit(config, DepositParams(account_id=account_id, amount=amount_e6))

if __name__ == "__main__":
    main()
