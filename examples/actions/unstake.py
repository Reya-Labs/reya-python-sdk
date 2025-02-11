from dataclasses import dataclass

@dataclass
class UnstakingParams:
    shares_amount: int
    min_out: int

def unstake(configs: dict, params: UnstakingParams) -> bool:
    w3 = configs['w3']
    passive_pool = configs['w3passive_pool']
    account_address = configs['w3account'].address
    rusd_address = configs['rusd_address']
    
    tx_hash = passive_pool.functions.removeLiquidityV2(1, [rusd_address, params.shares_amount, account_address, params.min_out]).transact({'from': account_address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    return tx_receipt