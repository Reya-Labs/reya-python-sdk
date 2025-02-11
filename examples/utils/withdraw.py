from examples.utils import deposit_or_withdraw
from examples.utils.withdraw import DepositParams


def withdraw(configs: dict, params: DepositParams) -> bool:
    return deposit_or_withdraw(configs=configs, params=params, is_deposit=False)