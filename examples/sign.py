from dataclasses import dataclass
from eth_account.messages import encode_typed_data
from typing import Dict, Any


def convert_ethers_signature_to_eip712_signature(signature, deadline):
    return (
        signature['v'],
        signature['r'],
        signature['s'],
        deadline
    )


def sign_core_commands(
    signer,  # This should be a Web3 Account object or equivalent
    reya_chain_id,
    caller,
    account_id,
    commands,
    nonce,
    deadline,
    extra_signature_data,
    core_proxy_address
):
    # Define the types and values according to the EIP-712 standard
    commands_dict = []
    for command in commands:
        (command_type, inputs, market_id, exchange_id) = command
        commands_dict.append({
            'commandType': command_type,
            'inputs': inputs,
            'marketId': market_id,
            'exchangeId': exchange_id
        })
    types = {
        'ExecuteBySig': [
            {'name': 'verifyingChainId', 'type': 'uint256'},
            {'name': 'caller', 'type': 'address'},
            {'name': 'accountId', 'type': 'uint128'},
            {'name': 'commands', 'type': 'Command[]'},
            {'name': 'nonce', 'type': 'uint256'},
            {'name': 'deadline', 'type': 'uint256'},
            {'name': 'extraSignatureData', 'type': 'bytes'},
        ],
        'Command': [
            {'name': 'commandType', 'type': 'uint8'},
            {'name': 'inputs', 'type': 'bytes'},
            {'name': 'marketId', 'type': 'uint128'},
            {'name': 'exchangeId', 'type': 'uint128'},
        ]
    }

    value = {
        'verifyingChainId': reya_chain_id,
        'caller': caller,
        'accountId': account_id,
        'commands': commands_dict,
        'nonce': nonce,
        'deadline': deadline,
        'extraSignatureData': extra_signature_data,
    }

    signature = sign_reya_typed_data(signer, core_proxy_address, types, value)

    return convert_ethers_signature_to_eip712_signature(signature, deadline)


def sign_reya_typed_data(signer, verifying_contract, types, value):
    domain = get_reya_domain(verifying_contract)

    encoded_data = encode_typed_data(
        domain_data=domain, message_types=types, message_data=value)
    signed_message = signer.sign_message(encoded_data)

    return {
        'v': signed_message.v,
        'r': signed_message.r.to_bytes(32, byteorder='big'),
        's': signed_message.s.to_bytes(32, byteorder='big')
    }


def get_reya_domain(verifying_contract) -> Dict[str, Any]:
    return {
        'name': 'Reya',
        'version': '1',
        'verifyingContract': verifying_contract,
    }
