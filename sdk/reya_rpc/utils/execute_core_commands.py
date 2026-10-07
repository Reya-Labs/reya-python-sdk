from typing import Any

from web3.types import TxReceipt

from sdk.reya_rpc.utils.transaction_utils import sign_and_send


def execute_core_commands(config: dict[str, Any], account_id: int, commands: list[Any]) -> TxReceipt:
    core = config["w3contracts"]["core"]
    return sign_and_send(config, core.functions.execute(account_id, commands))
