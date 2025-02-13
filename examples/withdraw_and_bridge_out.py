from reya_actions.actions import withdraw
from reya_actions.actions import WithdrawParams
from reya_actions import get_config
import os
from dotenv import load_dotenv
from reya_actions.actions.bridge_out import BridgeOutParams, bridge_out_to_arbitrum

def main():
    load_dotenv()
    account_id = int(os.environ['ACCOUNT_ID'])
    config = get_config()

    amount_e6 = int(1e6)
    withdraw(config, WithdrawParams(account_id=account_id, amount=amount_e6))
    result = bridge_out_to_arbitrum(config, BridgeOutParams(amount=amount_e6, fee_limit=int(0.01e18)))
    tx_hash = result['transaction_receipt'].transactionHash.hex()
    print(f'Check status of bridging transfer here: https://www.socketscan.io/tx/{tx_hash}')

if __name__ == "__main__":
    main()
