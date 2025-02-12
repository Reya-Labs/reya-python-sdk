from dataclasses import dataclass

@dataclass
class UnstakingParams:
    shares_amount: int
    min_out: int

def unstake(configs: dict, params: UnstakingParams) -> bool:
    w3 = configs['w3']
    account = configs['w3account']
    passive_pool = configs['w3contracts']['passive_pool']
    rusd = configs['w3contracts']['rusd']
    
    # Unstake rUSD from the passive pool
    tx_hash = passive_pool.functions.removeLiquidityV2(1, [rusd.address, params.shares_amount, account.address, params.min_out]).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print('Unstaked from passive pool:', tx_receipt.transactionHash.hex())

    return tx_receipt