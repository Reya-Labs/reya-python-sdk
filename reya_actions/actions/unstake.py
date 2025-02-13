from web3 import Web3
from hexbytes import HexBytes
from dataclasses import dataclass

@dataclass
class UnstakingParams:
    shares_amount: int
    min_tokens: int

def unstake(config: dict, params: UnstakingParams):
    w3 = config['w3']
    account = config['w3account']
    passive_pool = config['w3contracts']['passive_pool']
    
    # Unstake rUSD from the passive pool
    tx_hash = passive_pool.functions.removeLiquidity(1, params.shares_amount, params.min_tokens).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f'Unstaked from passive pool: {tx_receipt.transactionHash.hex()}')

    # Decode the logs to get the resulting shares amount
    logs = tx_receipt["logs"]
    event_sig = Web3.keccak(text="ShareBalanceUpdated(uint128,address,int256,uint256,int256,uint256,address,int256)").hex()
    filtered_logs = [log for log in logs if HexBytes(log["topics"][0]) == HexBytes(event_sig)]

    if not len(filtered_logs) == 1:
        raise Exception("Failed to decode transaction receipt for staking to passive pool")
    
    event = passive_pool.events.ShareBalanceUpdated().process_log(filtered_logs[0])
    token_amount = -int(event["args"]["tokenDelta"])

    return {
        'transaction_receipt': tx_receipt,
        'token_amount': token_amount,
    }