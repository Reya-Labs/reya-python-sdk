from dataclasses import dataclass

@dataclass
class StakingParams:
    amount: int
    min_shares: int

def stake(configs: dict, params: StakingParams) -> bool:
    w3 = configs['w3']
    passive_pool = configs['w3passive_pool']
    account_address = configs['w3account'].address
    rusd_address = configs['rusd_address']
    
    tx_hash = passive_pool.functions.addLiquidityV2(1, [rusd_address, params.amount, account_address, params.min_shares]).transact({'from': account_address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    return tx_receipt