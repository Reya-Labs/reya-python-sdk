from examples.utils.config import getConfigs
from examples.utils.consts import MarketIds
from examples.utils.update_prices import update_oracle_prices
from web3 import Web3
from decimal import *
from examples.utils.trade import MatchOrderParams, execute_trade
import os
from dotenv import load_dotenv

def get_sample_signed_payloads(chain_id):
    if chain_id == 1729:
        return [{
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

    return [{
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

def main():
    '''Example trade
    This executes a short trade on configured account, of base 0.1 (notional = base * price).
    The price limit constrains the slippage. For simplicity, any slippage is allowed, meaning the limit
    is 0 for short trades or max uint256 for long trades.

    The signed_payloads list only includes an update for the SOLUSD market, but to avoid staleness errors,
    all markets should be included and timestamps must be within seconds of the transaction execution.
    Payloads can be queried from Reya's websocket, on the price channel. The signatures are generated by a
    trusted producer, they are verified on-chain (do not modify).
    '''

    load_dotenv()
    account_id = int(os.environ['ACCOUNT_ID'])
    configs = getConfigs()

    # order inputs
    order_base = -0.1
    market_id = MarketIds.SOL.value
    price_limit = 0 if order_base < 0 else 1_000_000_000

    # input formatting
    scaled_abs_order_base = Web3.to_wei(abs(order_base), 'ether')
    actual_order_base = scaled_abs_order_base if order_base > 0 else -scaled_abs_order_base
    actual_price_limit = Web3.to_wei(price_limit, 'ether')

    # signed price payloads
    signed_payloads = get_sample_signed_payloads(chain_id=configs['chain_id'])

    update_oracle_prices(
        configs=configs, 
        signed_payloads=signed_payloads
    )

    execute_trade(
        configs=configs,
        params=MatchOrderParams(
            account_id=account_id,
            market_id=market_id,
            base=actual_order_base,
            price_limit=actual_price_limit
        )
    )


if __name__ == "__main__":
    main()
