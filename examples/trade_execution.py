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

        abs_order_base_e18 = Web3.to_wei(abs(order_base), 'ether')
        order_base_e18 = abs_order_base_e18 if order_base > 0 else -abs_order_base_e18
        price_limit_e18 = Web3.to_wei(price_limit, 'ether')

        result = trade(
            config=config,
            params=TradeParams(
                account_id=account_id,
                market_id=market_id,
                base=order_base_e18,
                price_limit=price_limit_e18
            )
        )

        print(f'Trade information: execution price = {result['execution_price'] / 1e18} and paid fees = {result['fees'] / 1e6} rUSD')

    # long trade
    trade_on_sol(order_base=0.1)

    # short trade
    trade_on_sol(order_base=-0.1)


if __name__ == "__main__":
    main()
