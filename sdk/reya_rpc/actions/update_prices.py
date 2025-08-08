import math

from eth_abi import encode
from web3 import Web3


def update_oracle_prices(config, signed_payloads):
    """
    Updates oracle prices on Reya DEX using price payloads signed by Stork.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and account information. Check out config.py for more details.
        signed_payloads (list): List of signed price payloads.

    Returns:
        dict: Contains transaction receipt of the oracle price update transaction.
    """

    # Retrieve relevant fields from config
    w3 = config["w3"]
    account = config["w3account"]
    multicall = config["w3contracts"]["multicall"]
    oracle_adapter = config["w3contracts"]["oracle_adapter"]

    # Generate oracle update calls
    calls = get_oracle_update_calls(
        oracle_adapter=oracle_adapter,
        signed_payloads=signed_payloads,
    )

    # Execute the batched oracle price update transaction
    tx_hash = multicall.functions.tryAggregatePreservingError(False, calls).transact({"from": account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Updated oracle prices: {tx_receipt.transactionHash.hex()}")

    # Return transaction receipt
    return {
        "transaction_receipt": tx_receipt,
    }


def get_oracle_update_calls(oracle_adapter, signed_payloads):
    """
    Constructs a list of encoded calls for updating oracle prices.

    Args:
        oracle_adapter (Contract): The OracleAdapter contract instance.
        signed_payloads (list): List of signed payloads containing price updates.

    Returns:
        list: Encoded calls for oracle price updates.
    """

    encoded_calls: list = []
    for signed_payload in signed_payloads:
        price_payload = signed_payload["pricePayload"]
        encoded_payload = encode(
            ["(address,(string,uint256,uint256),bytes32,bytes32,uint8)"],
            [
                [
                    signed_payload["oraclePubKey"],
                    [
                        price_payload["assetPairId"],
                        math.floor(int(price_payload["timestamp"]) / 1e9),
                        int(price_payload["price"]),
                    ],
                    Web3.to_bytes(hexstr=signed_payload["r"]),
                    Web3.to_bytes(hexstr=signed_payload["s"]),
                    signed_payload["v"],
                ]
            ],
        )

        encoded_calls.append(
            (
                oracle_adapter.address,
                oracle_adapter.encode_abi(fn_name="fulfillOracleQuery", args=[encoded_payload]),
            )
        )

    # Return the list of encoded oracle update calls
    return encoded_calls
