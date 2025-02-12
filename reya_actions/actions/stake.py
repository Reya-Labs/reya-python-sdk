from web3 import Web3
from hexbytes import HexBytes
from dataclasses import dataclass

@dataclass
class StakingParams:
    token_amount: int
    min_shares: int

def stake(config: dict, params: StakingParams):
    w3 = config['w3']
    account = config['w3account']
    passive_pool = config['w3contracts']['passive_pool']
    rusd = config['w3contracts']['rusd']

    # Approve the rUSD token to be used by the periphery
    tx_hash = rusd.functions.approve(passive_pool.address, params.token_amount).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print('Approved rUSD to core:', tx_receipt.transactionHash.hex())
    
    # Stake rUSD in the passive pool
    tx_hash = passive_pool.functions.addLiquidityV2(1, [rusd.address, params.token_amount, account.address, params.min_shares]).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print('Staked in passive pool:', tx_receipt.transactionHash.hex())

    # Decode the logs to get the resulting shares amount
    logs = tx_receipt["logs"]
    event_sig = Web3.keccak(text="ShareBalanceUpdated(uint128,address,int256,uint256,int256,uint256,address,int256)").hex()
    filtered_logs = [log for log in logs if HexBytes(log["topics"][0]) == HexBytes(event_sig)]

    if not len(filtered_logs) == 1:
        raise Exception("Failed to decode transaction receipt for staking to passive pool")
    
    event = passive_pool.events.ShareBalanceUpdated().process_log(filtered_logs[0])
    shares_amount = int(event["args"]["shareDelta"])

    return {
        'transaction_receipt': tx_receipt,
        'shares_amount': shares_amount,
    }