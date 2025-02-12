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
    
    tx_hash = passive_pool.functions.addLiquidityV2(1, [rusd.address, params.amount, account.address, params.min_shares]).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    return tx_receipt