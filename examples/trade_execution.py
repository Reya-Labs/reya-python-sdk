from web3 import Web3
from decimal import *
import argparse
from examples.utils.trade import execute_trade, getConfigs, MarketIds


def main(current_nonce):
    '''Example trade
    This executes a short trade on account 12, of base 1 (notional = base * price).
    The price limit constrains the slippage. For simplicity, any slippage is allowed, meaning the limit
    is 0 for short trades or max uint256 for long trades.

    The price_payloads list only includes an update for the SOL market, but to avoid staleness errors,
    all markets should be included and timestamps must be within seconds of the transaction execution.
    Payloads can be queried from Reya's websocket, on the price channel. The signatures are generated by a
    trusted producer, they are verified on-chain (do not modify).
    '''
    configs = getConfigs()

    # order inputs
    order_base = -0.1
    market_id = MarketIds.SOL.value
    price_limit = 0 if order_base < 0 else 1_000_000_000

    # price payloads
    price_payloads = [{
            'oraclePubKey': '0x0a803F9b1CCe32e2773e0d2e98b37E0775cA5d44',
            'pricePayload': {
                'assetPairId': 'SOLUSD',
                'timestamp': 1724082435245083759,  # in MS * 10 ^ 6
                'price': 144181178943749000000,  # WAD precision
            },
            # EIP712 signature of the price data
            'r': '0x66f5b1a073d52d93149b80b69bebb0bee563eebd4370c1dd9c04ff7c1d62f425',
            's': '0x6d5c4d7aad09748a2f234d09e28b6075b8103dd7e45b941d0c60093a3149fc00',
            'v': '0x1b',
        }]

    # input formatting
    scaled_abs_order_base = Web3.to_wei(abs(order_base), 'ether')
    actual_order_base = scaled_abs_order_base if order_base > 0 else -scaled_abs_order_base
    actual_price_limit = Web3.to_wei(price_limit, 'ether')

    execute_trade(
        configs=configs,
        base=actual_order_base,  # WAD precision
        price_limit=actual_price_limit,  # WAD precision
        market_id=market_id,
        account_id=configs['account_id'],  # your margin account id
        # sigature nonce of owner address stored in Reya Core
        current_core_nonce=int(current_nonce),
        # example payload, replace with new data
        price_payloads=price_payloads
    )


if __name__ == "__main__":
    # Parse script inputs
    parser = argparse.ArgumentParser(
        description="Example script for demonstration.")
    parser.add_argument('--current-nonce', type=str, default="1",
                        help="Current nonce of the margin account owner, tracked in Core")

    args = parser.parse_args()
    main(args.current_nonce)
