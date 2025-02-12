from reya_actions import get_config, update_oracle_prices

def main():
    config = get_config()

    if config['chain_id'] == 1729:
        sample_signed_payloads = [{
            'oraclePubKey': '0x51aa9e9C781F85a2C0636A835EB80114c4553098',
            'pricePayload': {
                'assetPairId': 'SOLUSDMARK',
                'timestamp': '1731512244875654000',  # in MS * 10 ^ 6
                'price': '215003440749999000000',  # WAD precision
            },
            # EIP712 signature of the price data
            'r': '0x3820594933f6d003885a51bfc62b13f0518edae89507d6e89a022cf36975c49f',
            's': '0x3c45a271ca6164a62f8b91ff21cf22d36399f5e406e8e8bbbcfdf799f905243e',
            'v': 28,
        }]
    else:
        sample_signed_payloads = [{
            'oraclePubKey': '0x0a803F9b1CCe32e2773e0d2e98b37E0775cA5d44',
            'pricePayload': {
                'assetPairId': 'SOLUSD',
                'timestamp': '1724082435245083759',  # in MS * 10 ^ 6
                'price': '144181178943749000000',  # WAD precision
            },
            # EIP712 signature of the price data
            'r': '0x66f5b1a073d52d93149b80b69bebb0bee563eebd4370c1dd9c04ff7c1d62f425',
            's': '0x6d5c4d7aad09748a2f234d09e28b6075b8103dd7e45b941d0c60093a3149fc00',
            'v': 27,
        }]

    update_oracle_prices(
        config=config, 
        signed_payloads=sample_signed_payloads
    )


if __name__ == "__main__":
    main()
