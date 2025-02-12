from dataclasses import dataclass

@dataclass
class StakingParams:
    amount: int
    min_shares: int

def stake(configs: dict, params: StakingParams) -> bool:
    w3 = configs['w3']
    account = configs['w3account']
    passive_pool = configs['w3contracts']['passive_pool']
    rusd = configs['w3contracts']['rusd']

    # Approve the rUSD token to be used by the periphery
    tx_hash = rusd.functions.approve(passive_pool.address, params.amount).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print('Approved rUSD to core:', tx_receipt.transactionHash.hex())
    
    # Stake rUSD in the passive pool
    tx_hash = passive_pool.functions.addLiquidityV2(1, [rusd.address, params.amount, account.address, params.min_shares]).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print('Staked in passive pool:', tx_receipt.transactionHash.hex())

    return tx_receipt