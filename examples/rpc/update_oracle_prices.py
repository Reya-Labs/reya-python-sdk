from sdk.reya_rpc import get_config, update_oracle_prices


def main():
    """
    Example script demonstrating how to update oracle prices on Reya DEX
    using signed price payloads.
    """

    # Load configuration
    config = get_config()

    # Define sample signed price payloads
    if config["chain_id"] == 1729:
        sample_signed_payloads = [
            {
                "oraclePubKey": "0x51aa9e9C781F85a2C0636A835EB80114c4553098",
                "pricePayload": {
                    "assetPairId": "SOLUSDMARK",
                    "timestamp": "1731512244875654000",  # in nanoseconds
                    "price": "215003440749999000000",  # scaled by 10^18
                },
                # EIP712 signature of the price data
                "r": "0x3820594933f6d003885a51bfc62b13f0518edae89507d6e89a022cf36975c49f",
                "s": "0x3c45a271ca6164a62f8b91ff21cf22d36399f5e406e8e8bbbcfdf799f905243e",
                "v": 28,
            }
        ]
    else:
        sample_signed_payloads = [
            {
                "oraclePubKey": "0x0a803F9b1CCe32e2773e0d2e98b37E0775cA5d44",
                "pricePayload": {
                    "assetPairId": "SOLUSD",
                    "timestamp": "1724082435245083759",  # in nanoseconds
                    "price": "144181178943749000000",  # sclaed by 10^18
                },
                # EIP712 signature of the price data
                "r": "0x66f5b1a073d52d93149b80b69bebb0bee563eebd4370c1dd9c04ff7c1d62f425",
                "s": "0x6d5c4d7aad09748a2f234d09e28b6075b8103dd7e45b941d0c60093a3149fc00",
                "v": 27,
            }
        ]

    # Execute the oracle price update transaction
    update_oracle_prices(config=config, signed_payloads=sample_signed_payloads)


if __name__ == "__main__":
    main()
