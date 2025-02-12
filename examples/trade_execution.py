from web3 import Web3
from reya_actions import MarketIds, TradeParams
from reya_actions import get_config, trade
import os
from dotenv import load_dotenv

def main():
    load_dotenv()
    account_id = int(os.environ['ACCOUNT_ID'])
    config = get_config()

    def trade_on_sol(order_base):
        market_id = MarketIds.SOL.value
        price_limit = 0 if order_base < 0 else 1_000_000_000

        scaled_abs_order_base = Web3.to_wei(abs(order_base), 'ether')
        actual_order_base = scaled_abs_order_base if order_base > 0 else -scaled_abs_order_base
        actual_price_limit = Web3.to_wei(price_limit, 'ether')

        result = trade(
            config=config,
            params=TradeParams(
                account_id=account_id,
                market_id=market_id,
                base=actual_order_base,
                price_limit=actual_price_limit
            )
        )

        print("Trade information:", "execution price:", result['execution_price'] / 1e18, "and paid fees:", result['fees'] / 1e6)

    # long trade
    trade_on_sol(order_base=0.1)

    # short trade
    trade_on_sol(order_base=-0.1)


if __name__ == "__main__":
    main()
