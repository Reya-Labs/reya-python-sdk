from typing import Any

from web3.types import TxReceipt


def execute_core_commands(config: dict[str, Any], account_id: int, commands: list[Any]) -> TxReceipt:
    w3 = config["w3"]
    account = config["w3account"]
    core = config["w3contracts"]["core"]

    # Build the transaction
    tx = core.functions.execute(account_id, commands).build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "chainId": config["chain_id"],
        }
    )

    # Sign the transaction
    signed_tx = w3.eth.account.sign_transaction(tx, private_key=account.key)

    # Send the raw transaction
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

    # Wait for the transaction receipt
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    return tx_receipt  # type: ignore[no-any-return]
